"""Tools exposed to the Q&A agent (plain JSON in/out, MCP-ready). Every tool
1) validates and normalises its arguments, 2) checks the user's role/scope (perm.py) BEFORE computing,
3) runs deterministic Python (engine.py), 4) strips fields the role may not see, 5) never returns free-text Remark,
6) passes the result through a scope guard that removes any row naming a part outside the user's scope."""
from __future__ import annotations

import json
import math
import re

from . import engine as E
from .normalize import COMMODITIES, PART_ANY, PLANTS, norm_partno, norm_plant, part_type_of
from .perm import Denied, User, aggregate, line_scope

MAX_ROWS = 80


def _scope_filter(user: User, parts):
    out = []
    for p in parts:
        try:
            user.check_part(p)
            out.append(p)
        except Denied:
            pass
    return out


def search_parts(user: User, query: str):
    """Find internal part numbers by manufacturer part number or description words (e.g. 'RP2040', 'NEMA17')."""
    D = E.data()
    words = [w for w in re.split(r"\s+", query.strip().lower()) if w]
    if not words:
        raise ValueError("query is empty")
    hits = []
    for p, r in D.parts.items():
        text = " ".join(str(r.get(k) or "") for k in ("PartNo", "Description", "MfrPartNo")).lower()
        if all(w in text for w in words):
            mpn = str(r.get("MfrPartNo") or "").lower()
            rank = 0 if mpn == query.strip().lower() else (1 if mpn.startswith(words[0]) else (2 if r["PartType"] == "COMPONENT" else 3))
            hits.append((rank, p))
    hits = _scope_filter(user, [p for _, p in sorted(hits)])
    rows = [{"part": p, "description": D.parts[p]["Description"], "type": D.parts[p]["PartType"]} for p in hits[:25]]
    return {"query": query, "matches": rows, "count": len(hits)}


def part_info(user: User, part: str):
    D = E.data()
    rec = user.filter_record(dict(D.parts[part]))
    out = {"part": rec, "attributes": D.attrs.get(part, {})}
    if user.sensitive_level() == "aggregate":
        out["note_sensitive"] = "Supplier, manufacturer and MPN are visible to your role only as aggregates (supplier_price_stats)."
    if user.price_level() != "detail":
        out["note_price"] = "The unit cost of an individual part is not visible to your role." + (
            " Statistics over a predefined group of 3+ parts are available via supplier_price_stats." if user.price_level() == "aggregate" else "")
    return out


def bom_explode(user: User, root: str, qty: float = 1, date: str = "", commodity: str = ""):
    ex = E.explode(root, qty, date or E.BASELINE)
    D, items = E.data(), {}
    for p in ex["positions"]:
        if commodity and not p["component"].startswith(commodity.upper() + "-"):
            continue
        it = items.setdefault(p["component"], {"component": p["component"], "description": D.parts[p["component"]]["Description"],
                                               "uom": D.parts[p["component"]]["UoM"], "positions": []})
        it["positions"].append({"parent": p["parent"], "refdes": p["refdes"], "qty_per_unit": round(p["qty_per_unit"], 4)})
    visible = _scope_filter(user, list(items))
    rows = [dict(items[k], gross=ex["gross"][k]) for k in visible]
    return {"root": root, "qty": qty, "production_date": ex["date"], "component_count": len(rows), "items": rows[:MAX_ROWS],
            "truncated": len(rows) > MAX_ROWS, "bom_lines_not_effective_excluded": ex["excluded_lines_not_effective"],
            "rule": "line effective if EffectiveFrom <= production date <= EffectiveTo; gross = qty x product(QtyPer x (1 + scrap)); "
                    "PCS rounded up, metres rounded up to 0.01"}


def where_used(user: User, part: str, date: str = ""):
    w = E.where_used(part, date or None)
    return {"part": part, "date": date or "any", "direct_parents": _scope_filter(user, w["direct_parents"]),
            "products": _scope_filter(user, w["products"]), "note": "only parents inside your data scope are listed"}


def inventory(user: User, part: str, plant: str, date: str = ""):
    user.check_plant(plant)
    a = E.available(part, plant, date or E.BASELINE)
    return {**a, "rule": "available = sum(on hand - allocated) of RELEASED lots, unit consistent, not expired on the production date"}


def open_po(user: User, part: str, plant: str, date: str = ""):
    user.check_plant(plant)
    t = E.in_transit(part, plant, date or E.BASELINE)
    lines = [user.filter_record({"PONo": x["PONo"], "POLine": x["POLine"], "OpenQty": x["OpenQty"], "ETA": str(x["ETA"]),
                                 "SupplierName": x["SupplierName"], "UnitPriceUSD": x["UnitPriceUSD"]}) for x in t["po_lines"]]
    return {"part": part, "plant": plant, "production_date": t["date"], "in_transit_counted": t["in_transit"], "po_lines": lines,
            "lines_excluded": t["lines_excluded"],
            "rule": f"open lines only; open qty = ordered - received; counted if ETA + {E.INSPECTION_DAYS} inspection days <= production date"}


def alternates(user: User, primary: str, parent: str = "", date: str = "", product: str = ""):
    """parent optional: without it the position-independent rules are checked and each record's scope is listed."""
    if parent:
        prods, _ = E.product_context(parent)
        if user.role == "product_owner":
            prods = _scope_filter(user, prods)
        product = product or (prods[0] if prods else "")
    o = E.alternate_options(primary, parent, product, date or E.BASELINE)
    if not parent:
        o["note"] = "no parent given: ASSEMBLY scope not evaluated; each record's scope is listed. Records are never chained."
    if user.role == "product_owner":
        allowed = line_scope(user.scope)
        other = "LINE " + ("PRN" if user.scope == "BRD" else "BRD")
        o["usable"] = [u for u in o["usable"] if u["substitute"] in allowed and u.get("scope") != other]
        o["rejected"] = [r for r in o["rejected"] if r.get("substitute") in allowed and r.get("scope") != other]
    return o


def shortage_simulation(user: User, product: str, qty: float, plant: str, date: str, include_open_po: bool = False,
                        include_alternates: bool = False):
    user.check_plant(plant)
    if include_alternates:
        r = E.shortage_after_alternates(product, qty, plant, date, include_po=include_open_po)
        items = [{k: v for k, v in i.items() if k != "po_excluded"} for i in r["items"] if i["short"] > 0]
        m = E.max_buildable(product, plant, date, include_po=include_open_po, use_alternates=True, upper=int(qty))
        return {"product": product, "qty": qty, "plant": plant, "production_date": date, "include_open_po": include_open_po,
                "short_before_and_after_substitutes": items,
                "still_short": [{"component": i["component"], "still_short": i["still_short"]} for i in items if i["still_short"] > 0],
                "max_units_buildable": m["max_units"], "limiting_parts": m["limiting_parts"],
                "rules": f"PO: open, ETA + {E.INSPECTION_DAYS} days <= production date. Substitutes: approval, validity, direction, "
                         "no chaining, scope, lifecycle, spec check, ratio, no mixing, matched sets, priority."}
    s = E.shortage(product, qty, plant, date, include_po=include_open_po)
    return {"product": product, "qty": qty, "plant": plant, "production_date": s["date"], "include_open_po": include_open_po,
            "short_items": s["short_items"], "components_checked": len(s["all_items"]),
            "bom_lines_not_effective_excluded": s["bom_lines_not_effective"], "rules": s["rules"]}


def max_buildable(user: User, product: str, plant: str, date: str, include_open_po: bool = True, use_alternates: bool = True):
    user.check_plant(plant)
    return E.max_buildable(product, plant, date, include_po=include_open_po, use_alternates=use_alternates)


def substitution_capacity(user: User, primary: str, parent: str, plant: str, date: str, include_open_po: bool = False, product: str = ""):
    user.check_plant(plant)
    return E.substitution_capacity(primary, parent, plant, date, include_open_po, product=product or None)


def position_capacity(user: User, assembly: str, refdes: str, plant: str, date: str, qty: float, include_open_po: bool = False):
    user.check_plant(plant)
    return E.position_capacity(assembly, refdes, plant, date, qty, include_open_po)


def supplier_price_stats(user: User, parts: list | None = None, group: str = ""):
    """Supplier and unit-cost information obeying sensitivity: detail for component_engineer (cost) / plant_lead (supplier),
    aggregates for planner/product_owner. group = a product/assembly part number (its BOM components) or a 2-letter commodity code."""
    D = E.data()
    g = (norm_partno(group) or str(group).strip().upper()) if group else ""
    if g:
        if g in D.parts and part_type_of(g) != "COMPONENT":
            user.check_part(g)
            parts = sorted({p["component"] for p in E.explode(g, 1, E.BASELINE)["positions"]})
        elif g in COMMODITIES:
            parts = sorted(p for p, r in D.parts.items() if r["Commodity"] == g)
        else:
            raise ValueError(f"unknown group '{group}' (use a product/assembly part number or a commodity code such as CA)")
    parts = sorted({norm_partno(p) for p in (parts or []) if norm_partno(p)})  # dedupe BEFORE the fewer-than-3 rule
    if g:
        parts = _scope_filter(user, parts)
    for p in parts:
        user.check_part(p)
    recs = [D.parts[p] for p in parts if p in D.parts]
    out = {"parts": len(recs)}
    if user.sensitive_level() == "detail":
        out["suppliers"] = {r["PartNo"]: r["SupplierName"] for r in recs}
    elif user.sensitive_level() == "aggregate":
        out["suppliers_aggregate"] = aggregate([r["SupplierName"] for r in recs], "distinct_count")
        out["note_supplier"] = "Supplier names are sensitive: your role sees only aggregates."
    if user.price_level() == "detail":
        out["unit_cost_usd"] = {r["PartNo"]: r["UnitCostUSD"] for r in recs}
    elif user.price_level() == "aggregate":
        if not g:  # arbitrary lists would let overlapping queries be subtracted to recover one part's cost
            out["unit_cost_aggregate"] = "only available per predefined group: pass group = a product/assembly or a commodity code"
        else:
            out["group"] = g
            out["unit_cost_aggregate"] = aggregate([r["UnitCostUSD"] for r in recs], "stats")
        out["note_price"] = "Unit costs are restricted: only statistics over predefined groups that do not identify parts."
    else:
        out["note_price"] = "Unit costs are restricted and not visible to your role at all."
    return out


def lead_time_impact(user: User, part: str, new_lead_days: int, from_date: str, product_line: str = ""):
    """Who uses the part (in scope), supply per plant, open POs, substitutes - for lead-time-change questions."""
    D = E.data()
    w = E.where_used(part, from_date)
    prods = _scope_filter(user, w["products"])
    if product_line:
        prods = [f for f in prods if f.startswith(product_line.upper()[:3])]
    plants = {}
    for pl in PLANTS:
        try:
            user.check_plant(pl)
        except Denied:
            continue
        pos = [x for x in D.po_by.get(part, []) if x["Plant"] == pl and x["Status"] == "Open"]
        plants[pl] = {"available_on_date": E.available(part, pl, from_date)["available"],
                      "open_po": [user.filter_record({"PONo": x["PONo"], "OpenQty": x["OpenQty"], "ETA": str(x["ETA"]),
                                                      "SupplierName": x["SupplierName"], "UnitPriceUSD": x["UnitPriceUSD"]}) for x in pos]}
    parents = _scope_filter(user, w["direct_parents"])
    alts = {par: alternates(user, part, par, from_date) for par in parents}
    return {"part": part, "current_lead_time_days": D.parts[part]["LeadTimeDays"], "new_lead_time_days": new_lead_days,
            "from_date": from_date, "orders_placed_on_from_date_arrive_around": str(E.d_(from_date) + E.timedelta(days=int(new_lead_days))),
            "products_using_it": prods, "direct_parents": parents, "supply_by_plant": plants,
            "substitutes_by_parent": {k: {"usable": v["usable"], "rejected": v["rejected"]} for k, v in alts.items()}}


def pending_review(user: User):
    if user.role != "component_engineer":
        raise Denied("Only component engineers can see and decide pending review items.")
    items = [p for p in E.data().pending if user.can_approve(p["commodity"])]
    dec = E.Data.decisions()
    return {"count": len(items), "items": [{**{k: p[k] for k in ("id", "kind", "key", "commodity")},
                                            "decision": dec.get(p["key"], {}).get("decision", "open")} for p in items[:30]]}


TOOLS = {f.__name__: f for f in [search_parts, part_info, bom_explode, where_used, inventory, open_po, alternates,
                                  shortage_simulation, max_buildable, substitution_capacity, position_capacity,
                                  supplier_price_stats, lead_time_impact, pending_review]}

S, N, I, B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
DATE = {"type": "string", "description": "production date YYYY-MM-DD; default = data baseline 2027-03-01"}
PLANT = {"type": "string", "description": "plant code NRG (North Ridge), BHV (Bay Haven) or ELM (Elmstead)"}


def _schema(name, desc, props, req=()):
    return {"name": name, "description": desc, "input_schema": {"type": "object", "properties": props, "required": list(req)}}


SCHEMAS = [
    _schema("search_parts", "Find internal part numbers (e.g. MC-1001) from a manufacturer part number or description words such as 'RP2040', 'NCP1117', 'LM8UU'. Use this first when the user names a part by its real-world name.", {"query": S}, ["query"]),
    _schema("part_info", "Part master record (fields your role may see) and normalised attributes of one part.", {"part": S}, ["part"]),
    _schema("bom_explode", "Multi-level BOM explosion of a product (BRD-UNO3, BRD-PICO, BRD-PICOH, PRN-MK3S) or assembly (ASM-xxxxx) to components for qty units on a production date; optional 2-letter commodity filter like 'CA'.", {"root": S, "qty": N, "date": DATE, "commodity": S}, ["root"]),
    _schema("where_used", "Direct parents and products that use a part (only those inside the user's scope).", {"part": S, "date": DATE}, ["part"]),
    _schema("inventory", "Available stock of a part at a plant on a production date, with the lots that were excluded and why.", {"part": S, "plant": PLANT, "date": DATE}, ["part", "plant"]),
    _schema("open_po", "In-transit supply (open PO lines arriving in time incl. inspection days) for a part at a plant.", {"part": S, "plant": PLANT, "date": DATE}, ["part", "plant"]),
    _schema("alternates", "Check every substitute record of a primary part against the rules (approval, validity, direction, no chaining, scope, lifecycle, spec check, ratio, mixing, matched sets, priority): usable options and rejected ones with record ID and reason. Give parent (direct parent assembly) when known.", {"primary": S, "parent": S, "date": DATE, "product": S}, ["primary"]),
    _schema("shortage_simulation", "Shortage for producing qty of a product at a plant on a date. include_open_po adds POs arriving in time; include_alternates applies legal substitutes and returns what is still short plus max units buildable.", {"product": S, "qty": N, "plant": PLANT, "date": DATE, "include_open_po": B, "include_alternates": B}, ["product", "qty", "plant", "date"]),
    _schema("max_buildable", "Maximum product units buildable at a plant on a date from available stock (+ open POs, + legal substitutes) and the limiting parts.", {"product": S, "plant": PLANT, "date": DATE, "include_open_po": B, "use_alternates": B}, ["product", "plant", "date"]),
    _schema("substitution_capacity", "For a primary part on a direct parent assembly: legal substitutes, which parts must switch together (matched set) and how many assemblies the substitutes' stock covers.", {"primary": S, "parent": S, "plant": PLANT, "date": DATE, "include_open_po": B, "product": S}, ["primary", "parent", "plant", "date"]),
    _schema("position_capacity", "Maximum assemblies buildable from the part(s) at one reference-designator position (e.g. Y1, PC1,PC2) incl. legal substitutes.", {"assembly": S, "refdes": S, "plant": PLANT, "date": DATE, "qty": N, "include_open_po": B}, ["assembly", "refdes", "plant", "date", "qty"]),
    _schema("supplier_price_stats", "Supplier names and unit costs, automatically restricted by role (detail / aggregate / none).", {"parts": {"type": "array", "items": S}, "group": {"type": "string", "description": "product/assembly part number (its BOM components) or commodity code like CA; required for cost statistics of aggregate-only roles"}}),
    _schema("lead_time_impact", "Impact of a lead-time change: products using the part, supply by plant, open POs, substitutes.", {"part": S, "new_lead_days": I, "from_date": DATE, "product_line": S}, ["part", "new_lead_days", "from_date"]),
    _schema("pending_review", "Component engineers only: pending review items for the engineer's commodities.", {}),
]

PART_ARGS = ("part", "primary", "root", "product", "assembly", "parent")


def _from_mpn(raw):
    """An exact, unique manufacturer part number (e.g. 'RP2040') resolves to the internal number; anything else -> None."""
    key = str(raw or "").strip().upper()
    hits = [p for p, r in E.data().parts.items() if key and str(r.get("MfrPartNo") or "").upper() == key]
    return hits[0] if len(hits) == 1 else None


def _validate(user: User, args: dict) -> dict:
    """Normalise user/LLM arguments exactly like the cleaner; reject unknown plants, parts, dates and bad quantities."""
    a = dict(args)
    for k in PART_ARGS:
        if a.get(k):
            raw = a[k]
            a[k] = norm_partno(raw) or _from_mpn(raw)
            if not a[k]:
                raise ValueError(f"invalid part number '{raw}' (use search_parts to find the internal number)")
            user.check_part(a[k])  # uniform denial: does not reveal whether an out-of-scope part exists
            if a[k] not in E.data().parts:
                raise ValueError(f"{a[k]} is not in the cleaned part master (invalid, unknown or waiting for review).")
    if "plant" in a:
        p = norm_plant(a["plant"])
        if not p:
            raise ValueError(f"unknown plant '{a['plant']}' (valid: {', '.join(PLANTS)}).")
        a["plant"] = p
    if "qty" in a:
        q = float(a["qty"])
        if not math.isfinite(q) or q <= 0:
            raise ValueError("qty must be a positive number.")
        a["qty"] = q
    for k in ("date", "from_date"):
        if a.get(k):
            E.d_(a[k])  # raises ValueError on an unsupported date
    return a


def _scope_guard(user: User, obj):
    """Defence in depth for scoped roles: drop every returned row / key / list item that names a part outside the
    user's scope (e.g. other commodities inside an authorised assembly, second-hop substitutes, matched-set members)."""
    if user.role not in ("product_owner", "component_engineer"):
        return obj, 0
    dropped = 0

    def bad(v):
        if isinstance(v, str):
            for m in PART_ANY.findall(v.upper()):
                if norm_partno(m) is None:
                    continue
                try:
                    user.check_part(m)
                except Denied:
                    return True
        return False

    def walk(o):
        nonlocal dropped
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if bad(k):
                    dropped += 1
                    continue
                if bad(v):
                    return None  # this record names an out-of-scope part -> the whole record is removed
                w = walk(v)
                if w is None and v is not None:
                    dropped += 1
                    continue
                out[k] = w
            return out
        if isinstance(o, (list, tuple)):
            out = []
            for v in o:
                w = None if bad(v) else walk(v)
                if w is None and v is not None:
                    dropped += 1
                    continue
                out.append(w)
            return out
        return o

    res = walk(obj)
    return (res if res is not None else {}), dropped


CAPACITY_KEYS = {"max_units_buildable", "max_units", "limiting_parts", "max_assemblies", "max_assemblies_uncapped",
                 "assemblies_from_substitutes", "max_assemblies_with_this_option"}


def _withhold(o):
    """Capacity numbers that depend on hidden parts are withheld at every level."""
    if isinstance(o, dict):
        for k in list(o):
            if k in CAPACITY_KEYS:
                o[k] = "withheld: depends on parts outside your data scope"
            else:
                _withhold(o[k])
    elif isinstance(o, list):
        for v in o:
            _withhold(v)


def run(user: User, name: str, args: dict) -> dict:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    try:
        args = _validate(user, args or {})
        if user.role == "auditor":
            raise Denied("The Auditor role cannot see any parts data.")
        res = TOOLS[name](user, **args)
        res, dropped = _scope_guard(user, res)
        if dropped:
            res["scope_filtered"] = f"{dropped} result entries outside your data scope were removed."
            if name in ("shortage_simulation", "max_buildable", "substitution_capacity", "position_capacity"):
                _withhold(res)
        return res
    except ValueError as e:
        return {"error": str(e)}
    except Denied as e:
        return {"permission_denied": str(e)}
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}
    except KeyError as e:
        return {"error": f"not found: {e}"}


def call(user: User, name: str, args: dict) -> str:
    return json.dumps(run(user, name, args), ensure_ascii=False, default=str)
