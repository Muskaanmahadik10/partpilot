"""Deterministic calculation layer (no LLM): BOM explosion, where-used, available stock, in-transit supply,
substitute-part rules, shortage simulation and maximum buildable quantity.
Every function returns the rules applied and the excluded record counts so answers can explain their basis.

Substitute (alternate) rules, checked per position product -> parent assembly -> component on a production date:
  approval      the record must be Approved (Pending only after the responsible engineer approves it in PartPilot)
  validity      production date inside ValidFrom..ValidTo
  direction     a record works primary -> substitute; the reverse only when TwoWay = Y
  no chaining   A->B and B->C never gives A->C
  scope         GLOBAL, LINE (product line of the product being built) or ASSEMBLY (the direct parent)
  lifecycle     the substitute must be Active or NRND
  spec check    voltage/current >= primary, tolerance <= primary, dielectric at least as good, same package, RoHS = Y,
                same value (capacitance, resistance, inductance, output voltage) unless QtyRatio != 1 or the record is
                part of an engineered matched set
  quantity      substitute qty = primary qty x QtyRatio
  no mixing     MixAllowed = N: one parent assembly uses all-primary or all-substitute at that position
  matched set   records sharing a SetID switch together, every member must pass on the same parent
  priority      lower Priority is used first
"""
from __future__ import annotations

import json
import math
import os
import pickle
from collections import defaultdict
from datetime import date, timedelta

from .normalize import DIELECTRIC_RANK, line_of_product, part_type_of

CLEAN = os.getenv("PARTPILOT_CLEAN_DIR", "data_clean")
STATE = os.getenv("PARTPILOT_STATE_DIR", "state")
BASELINE = date(2027, 3, 1)
INSPECTION_DAYS = 2  # incoming inspection after the PO arrives before the stock can be used


class Data:
    def __init__(self):
        with open(os.path.join(CLEAN, "clean.pkl"), "rb") as f:
            d = pickle.load(f)
        self.parts, self.attrs, self.bom, self.alts = d["parts"], d["attrs"], d["bom"], d["alternates"]
        self.inv, self.po, self.pending = d["inventory"], d["po"], d["pending"]
        self.children, self.parents = defaultdict(list), defaultdict(list)
        for ln in self.bom:
            self.children[ln["Parent"]].append(ln)
            self.parents[ln["Child"]].append(ln)
        self.alt_by_part = defaultdict(list)
        for a in self.alts:
            self.alt_by_part[a["Primary"]].append(a)
            self.alt_by_part[a["Alt"]].append(a)
        self.inv_by, self.po_by = defaultdict(list), defaultdict(list)
        for x in self.inv:
            self.inv_by[x["PartNo"]].append(x)
        for x in self.po:
            self.po_by[x["PartNo"]].append(x)

    @staticmethod
    def decisions():
        p = os.path.join(STATE, "approvals.json")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def approved_pending(self):
        return {k: v for k, v in self.decisions().items() if v.get("decision") == "approve"}


_D = None


def data() -> Data:
    global _D
    if _D is None:
        _D = Data()
    return _D


def reload():
    global _D
    _D = None
    from . import perm
    perm.clear_caches()


def d_(s):
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s)) if s else BASELINE


def eff(ln, day):
    return ln["From"] <= day and (ln["To"] is None or day <= ln["To"])


def round_up(q, uom):
    if uom == "M":
        return math.ceil(round(q * 100, 6)) / 100
    return math.ceil(round(q, 6))


# ---------------- BOM ----------------
def explode(root: str, qty: float, day) -> dict:
    """Multi-level explosion to components. Returns positions (component under its direct parent) and gross need."""
    D, day = data(), d_(day)
    positions, inactive, missing = [], 0, []

    def walk(parent, mult, path):
        nonlocal inactive
        kids = D.children.get(parent, [])
        if not kids:
            missing.append(parent)
        for ln in kids:
            if not eff(ln, day):
                inactive += 1
                continue
            q = mult * ln["Qty"] * (1 + ln["Scrap"])
            if part_type_of(ln["Child"]) == "ASSEMBLY":
                walk(ln["Child"], q, path + [ln["Child"]])
            else:
                positions.append({"component": ln["Child"], "parent": parent, "line": ln["LineNo"], "refdes": ln["RefDes"],
                                  "qty_per_unit": q / qty, "qty": q, "per_parent": ln["Qty"] * (1 + ln["Scrap"]),
                                  "parents_per_unit": mult / qty, "path": path + [ln["Child"]]})
    walk(root, float(qty), [root])
    gross = defaultdict(float)
    for p in positions:
        gross[p["component"]] += p["qty"]
    gross = {k: round_up(v, D.parts[k]["UoM"]) for k, v in gross.items()}
    return {"root": root, "qty": qty, "date": str(day), "positions": positions, "gross": gross,
            "excluded_lines_not_effective": inactive, "assemblies_without_bom": sorted(set(missing))}


def where_used(part: str, day=None) -> dict:
    """All ancestors up to products, only lines effective on `day` (default: any valid line)."""
    D = data()
    day = d_(day) if day else None
    direct, products, seen = set(), set(), set()
    stack = [part]
    while stack:
        n = stack.pop()
        for ln in D.parents.get(n, []):
            if day and not eff(ln, day):
                continue
            p = ln["Parent"]
            if n == part:
                direct.add(p)
            if p in seen:
                continue
            seen.add(p)
            if part_type_of(p) == "PRODUCT":
                products.add(p)
            stack.append(p)
    return {"part": part, "direct_parents": sorted(direct), "all_ancestors": sorted(seen), "products": sorted(products)}


# ---------------- supply ----------------
def available(part: str, plant: str, day) -> dict:
    """sum(on hand - allocated) of RELEASED lots with a consistent unit that are not expired on the production day."""
    D, day = data(), d_(day)
    total, used, ex = 0.0, 0, defaultdict(int)
    for x in D.inv_by.get(part, []):
        if x["Plant"] != plant:
            continue
        if x["QC"] != "RELEASED":
            ex[f"QC {x['QC'] or 'invalid value'}"] += 1
            continue
        if x["Shelf"]:
            if x["Mfg"] is None:
                ex["invalid date code on a shelf-life part"] += 1
                continue
            if x["Expiry"] < day:
                ex["expired before the production date"] += 1
                continue
        total += x["Avail"]
        used += 1
    return {"part": part, "plant": plant, "date": str(day), "available": round(total, 3), "lots_counted": used,
            "lots_excluded": dict(ex)}


def in_transit(part: str, plant: str, day) -> dict:
    """Open lines only; open qty = ordered - received; counted when ETA + inspection days <= production day."""
    D, day = data(), d_(day)
    total, lines, ex = 0.0, [], defaultdict(int)
    for x in D.po_by.get(part, []):
        if x["Plant"] != plant:
            continue
        if x["Status"] != "Open":
            ex[x["Status"]] += 1
            continue
        if x["ETA"] + timedelta(days=INSPECTION_DAYS) > day:
            ex[f"ETA + {INSPECTION_DAYS} inspection days after the production date"] += 1
            continue
        total += x["OpenQty"]
        lines.append(x)
    return {"part": part, "plant": plant, "date": str(day), "in_transit": round(total, 3), "po_lines": lines,
            "lines_excluded": dict(ex)}


# ---------------- substitutes ----------------
def spec_check(primary: str, sub: str, ratio: float, in_set: bool = False) -> list[str]:
    A, P = data().attrs.get(sub, {}), data().attrs.get(primary, {})
    bad = []
    for c in ("VoltageRating", "CurrentRating", "TempMaxC"):
        if c in P and (c not in A or A[c] < P[c] - 1e-9):
            bad.append(f"{c} lower or missing ({A.get(c, 'missing')} < {P[c]})")
    if "TolerancePct" in P and ("TolerancePct" not in A or A["TolerancePct"] > P["TolerancePct"]):
        bad.append("TolerancePct larger or missing")
    if "Dielectric" in P and DIELECTRIC_RANK.get(A.get("Dielectric"), -1) < DIELECTRIC_RANK[P["Dielectric"]]:
        bad.append(f"Dielectric worse or missing ({A.get('Dielectric', 'missing')} vs {P['Dielectric']})")
    if abs(ratio - 1) < 1e-9 and not in_set:
        for c in ("Capacitance", "Resistance", "Inductance", "OutputVoltage"):
            if c in P and (c not in A or abs(A[c] - P[c]) > 1e-9 * max(1, abs(P[c]))):
                bad.append(f"{c} differs or missing")
    if "Package" in P and A.get("Package") != P["Package"]:
        bad.append(f"Package differs ({A.get('Package', 'missing')} vs {P['Package']})")
    if A.get("RoHS") != "Y":
        bad.append("RoHS not Y")
    return bad


def alternate_options(primary: str, parent: str, product: str, day) -> dict:
    """Every record that touches `primary`, checked against the substitute rules for product -> parent -> primary on `day`.
    Returns usable options (by priority) and rejected ones with the rule that failed. Remark is never returned."""
    D, day = data(), d_(day)
    approved = D.approved_pending()
    usable, rejected = [], []
    for a in D.alt_by_part.get(primary, []):
        if a["Primary"] == primary:
            sub, ratio, direction = a["Alt"], a["QtyRatio"], "forward"
        elif a["TwoWay"] == "Y":
            sub, ratio, direction = a["Primary"], 1 / a["QtyRatio"], "reverse (TwoWay = Y)"
        else:
            rejected.append({"record": a["SubID"], "substitute": a["Primary"],
                             "rule": "direction: the record only allows primary -> substitute (TwoWay = N)"})
            continue
        reasons = []
        if a["Status"] == "Pending":
            if a["SubID"] not in approved:
                reasons.append("approval: status Pending (needs the engineer for the primary part's commodity to approve)")
        elif a["Status"] != "Approved":
            reasons.append(f"approval: status {a['Status'] or 'invalid value'}")
        if not (a["ValidFrom"] <= day and (a["ValidTo"] is None or day <= a["ValidTo"])):
            reasons.append(f"validity: not valid on {day} (valid {a['ValidFrom']}..{a['ValidTo'] or 'open'})")
        if product and a["Scope"] == "LINE" and line_of_product(product) != a["ScopeValue"]:
            reasons.append(f"scope: LINE {a['ScopeValue']} but the product is in line {line_of_product(product)}")
        if parent and a["Scope"] == "ASSEMBLY" and parent != a["ScopeValue"]:
            reasons.append(f"scope: ASSEMBLY {a['ScopeValue']} but the direct parent is {parent}")
        lc = D.parts.get(sub, {}).get("Lifecycle")
        if lc not in ("Active", "NRND"):
            reasons.append(f"lifecycle: substitute is {lc or 'invalid'}")
        bad = spec_check(primary, sub, ratio, in_set=bool(a["SetID"]))
        if bad:
            reasons.append("spec check: " + "; ".join(bad))
        rec = {"record": a["SubID"], "substitute": sub, "direction": direction, "ratio": ratio, "priority": a["Priority"],
               "scope": f"{a['Scope']} {a['ScopeValue'] or ''}".strip(), "mix_allowed": a["MixAllowed"],
               "matched_set": a["SetID"], "status": a["Status"] if not (a["Status"] == "Pending" and a["SubID"] in approved)
               else "Approved in PartPilot review"}
        if reasons:
            rejected.append({**rec, "rule": " | ".join(reasons)})
        else:
            usable.append(rec)
    usable.sort(key=lambda r: r["priority"])
    for u in usable:
        if u["matched_set"]:
            members = [a for a in D.alts if a["SetID"] == u["matched_set"]]
            u["set_members"] = [{"record": m["SubID"], "primary": m["Primary"], "substitute": m["Alt"], "ratio": m["QtyRatio"]}
                                for m in members]
    return {"primary": primary, "parent": parent, "product": product, "date": str(day), "usable": usable, "rejected": rejected,
            "rules": "approval, validity, direction, no chaining, scope, lifecycle, spec check, quantity ratio, "
                     "no mixing (MixAllowed = N), matched sets switch together, priority"}


def set_ok(set_id: str, parent: str, product: str, day) -> dict:
    """Matched set: every member must be individually usable at the same parent."""
    D = data()
    out = []
    for m in [a for a in D.alts if a["SetID"] == set_id]:
        opts = alternate_options(m["Primary"], parent, product, day)
        ok = any(u["record"] == m["SubID"] for u in opts["usable"])
        why = next((r["rule"] for r in opts["rejected"] if r.get("record") == m["SubID"]), "")
        out.append({"record": m["SubID"], "primary": m["Primary"], "substitute": m["Alt"], "ratio": m["QtyRatio"],
                    "usable": ok, "why_not": why})
    return {"matched_set": set_id, "members": out, "all_usable": bool(out) and all(x["usable"] for x in out)}


def _members(o, parent, product, day):
    members = [{"primary": None, "substitute": o["substitute"], "ratio": o["ratio"], "record": o["record"]}]
    if o["matched_set"]:
        b = set_ok(o["matched_set"], parent, product, day)
        if not b["all_usable"]:
            return None
        members = [{"primary": m["primary"], "substitute": m["substitute"], "ratio": m["ratio"], "record": m["record"]}
                   for m in b["members"]]
    return members


# ---------------- shortage ----------------
def shortage(product: str, qty: float, plant: str, day, include_po: bool = False) -> dict:
    ex = explode(product, qty, day)
    D, rows = data(), []
    for comp, need in sorted(ex["gross"].items()):
        av = available(comp, plant, day)
        sup = av["available"]
        po = in_transit(comp, plant, day) if include_po else None
        if po:
            sup += po["in_transit"]
        short = need - sup
        rows.append({"component": comp, "description": D.parts[comp]["Description"], "uom": D.parts[comp]["UoM"],
                     "gross": need, "available": av["available"], "in_transit": po["in_transit"] if po else None,
                     "short": round(short, 2) if short > 0 else 0, "lots_excluded": av["lots_excluded"],
                     "po_excluded": po["lines_excluded"] if po else None})
    return {"product": product, "qty": qty, "plant": plant, "date": ex["date"], "include_po": include_po,
            "short_items": [r for r in rows if r["short"] > 0], "all_items": rows,
            "bom_lines_not_effective": ex["excluded_lines_not_effective"],
            "rules": "gross = qty x product of (QtyPer x (1 + scrap)) per level, PCS rounded up; available = RELEASED lots "
                     "(on hand - allocated), not expired on the production day; in transit = open PO qty with "
                     f"ETA + {INSPECTION_DAYS} days <= production day"}


def max_buildable(product: str, plant: str, day, include_po=True, use_alternates=True, upper=None) -> dict:
    """Largest N such that every position is covered by primary stock + legally usable substitutes.
    With MixAllowed = N a parent assembly is all-primary or all-substitute, so substitution is counted in whole parent
    assemblies. A substitute's supply is shared with that part's own demand in the same build."""
    D, day = data(), d_(day)
    ex1 = explode(product, 1, day)
    per = defaultdict(float)
    for p in ex1["positions"]:
        per[p["component"]] += p["qty_per_unit"]
    supply = {}

    def sup(part):
        if part not in supply:
            s = available(part, plant, day)["available"]
            if include_po:
                s += in_transit(part, plant, day)["in_transit"]
            supply[part] = s
        return supply[part]
    opts_cache = {}

    def per_parent(comp, parent):
        return sum(x["per_parent"] for x in ex1["positions"] if x["component"] == comp and x["parent"] == parent)

    def feasible(n):
        remaining, plan = {}, []
        for comp, r in per.items():
            s = sup(comp)
            if round_up(n * r, D.parts[comp]["UoM"]) <= s + 1e-9:
                continue
            if not use_alternates:
                return False, plan
            deficit, covered = n * r - s, 0.0
            for p in [p for p in ex1["positions"] if p["component"] == comp]:
                key = (comp, p["parent"])
                if key not in opts_cache:
                    opts_cache[key] = alternate_options(comp, p["parent"], product, day)["usable"]
                max_asm = n * p["parents_per_unit"]
                for o in opts_cache[key]:
                    if covered >= deficit - 1e-9:
                        break
                    members = _members(o, p["parent"], product, day)
                    if members is None:
                        continue
                    for m in members:
                        m["primary"] = m["primary"] or comp
                    cap = math.inf
                    for m in members:
                        pa = per_parent(m["primary"], p["parent"])
                        if pa <= 0:
                            cap = 0
                            break
                        left = remaining.get(m["substitute"], sup(m["substitute"]) - per.get(m["substitute"], 0) * n)
                        cap = min(cap, math.floor(max(left, 0) / (pa * m["ratio"]) + 1e-9))
                    need_asm = math.ceil((deficit - covered) / p["per_parent"] - 1e-9)
                    use = int(min(cap, need_asm, max_asm))
                    if use <= 0:
                        continue
                    for m in members:
                        pa = per_parent(m["primary"], p["parent"])
                        remaining[m["substitute"]] = (remaining.get(m["substitute"], sup(m["substitute"]) - per.get(m["substitute"], 0) * n)
                                                      - use * pa * m["ratio"])
                    covered += use * p["per_parent"]
                    plan.append({"primary": comp, "parent": p["parent"], "assemblies_switched": use,
                                 "substitutes": [(m["primary"], m["substitute"], m["record"]) for m in members]})
            if covered < deficit - 1e-9:
                return False, plan
        return True, plan
    lo, hi = 0, int(upper) if upper else 100000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if feasible(mid)[0]:
            lo = mid
        else:
            hi = mid - 1
    ok, plan = feasible(lo)
    limiting = []
    if not feasible(lo + 1)[0]:
        # a part limits the build only if lo+1 is still infeasible when every OTHER short part has unlimited supply
        cand = [c for c, r in per.items() if round_up((lo + 1) * r, D.parts[c]["UoM"]) > sup(c) + 1e-9]
        saved = {c: supply[c] for c in cand}
        for c in cand:
            for o in cand:
                supply[o] = saved[o] if o == c else float("inf")
            if not feasible(lo + 1)[0]:
                limiting.append(c)
        supply.update(saved)
    return {"product": product, "plant": plant, "date": str(day), "max_units": lo, "substitution_plan": plan,
            "limiting_parts": limiting, "include_po": include_po, "use_alternates": use_alternates}


def shortage_after_alternates(product: str, qty: float, plant: str, day, include_po: bool = True) -> dict:
    """What is still short for `qty` units after (optionally) open POs and legally usable substitutes."""
    day = d_(day)
    base = shortage(product, qty, plant, day, include_po=include_po)
    ex1 = explode(product, 1, day)
    out = []
    for it in base["short_items"]:
        comp = it["component"]
        covered, used = 0.0, []
        for p in [p for p in ex1["positions"] if p["component"] == comp]:
            for o in alternate_options(comp, p["parent"], product, day)["usable"]:
                members = _members(o, p["parent"], product, day)
                if members is None:
                    continue
                cap, detail = math.inf, []
                for m in members:
                    m["primary"] = m["primary"] or comp
                    pa = sum(x["per_parent"] for x in ex1["positions"] if x["component"] == m["primary"] and x["parent"] == p["parent"])
                    s = available(m["substitute"], plant, day)["available"] + (
                        in_transit(m["substitute"], plant, day)["in_transit"] if include_po else 0)
                    own = sum(x["qty_per_unit"] for x in ex1["positions"] if x["component"] == m["substitute"]) * qty
                    c = math.floor(max(s - own, 0) / (pa * m["ratio"]) + 1e-9) if pa else 0
                    detail.append({"switch": f"{m['primary']} -> {m['substitute']}", "record": m["record"],
                                   "substitute_supply": s, "per_assembly": pa * m["ratio"], "assemblies_possible": c})
                    cap = min(cap, c)
                need_asm = math.ceil((it["short"] - covered) / p["per_parent"] - 1e-9)
                use = int(min(cap, need_asm))
                if use > 0:
                    covered += use * p["per_parent"]
                    used.append({"parent": p["parent"], "refdes": p["refdes"], "matched_set": o["matched_set"],
                                 "assemblies_switched": use, "members": detail})
        still = max(it["short"] - covered, 0)
        out.append({**it, "covered_by_substitutes": round(covered, 2), "still_short": round(still, 2), "substitutes_used": used})
    return {"product": product, "qty": qty, "plant": plant, "date": str(day), "include_po": include_po, "items": out}


def product_context(assembly: str):
    """Products (and their product line) that use an assembly - needed for LINE-scoped substitutes."""
    if part_type_of(assembly) == "PRODUCT":
        return [assembly], line_of_product(assembly)
    prods = where_used(assembly)["products"]
    lines = {line_of_product(f) for f in prods}
    return prods, (lines.pop() if len(lines) == 1 else None)


def substitution_capacity(primary: str, parent: str, plant: str, day, include_po: bool = False, product: str | None = None) -> dict:
    """For each usable substitute of `primary` on direct parent `parent`: which parts switch together (matched set)
    and how many parent assemblies the substitutes' supply covers (whole assemblies)."""
    D, day = data(), d_(day)
    prods, line = product_context(parent)
    product = product or (prods[0] if prods else "")
    per = defaultdict(float)
    for ln in [ln for ln in D.children.get(parent, []) if eff(ln, day)]:
        per[ln["Child"]] += ln["Qty"] * (1 + ln["Scrap"])
    res = []
    for o in alternate_options(primary, parent, product, day)["usable"]:
        members = _members(o, parent, product, day)
        if members is None:
            b = set_ok(o["matched_set"], parent, product, day)
            res.append({"option": o["substitute"], "matched_set": o["matched_set"], "usable": False,
                        "why": [m for m in b["members"] if not m["usable"]]})
            continue
        cap, det = math.inf, []
        for m in members:
            m["primary"] = m["primary"] or primary
            need = per.get(m["primary"], 0) * m["ratio"]
            s = available(m["substitute"], plant, day)["available"] + (in_transit(m["substitute"], plant, day)["in_transit"] if include_po else 0)
            c = math.floor(s / need + 1e-9) if need else 0
            det.append({"switch": f"{m['primary']} -> {m['substitute']}", "record": m["record"], "qty_per_assembly": need,
                        "substitute_supply": s, "assemblies_covered": c})
            cap = min(cap, c)
        res.append({"option": o["substitute"], "record": o["record"], "priority": o["priority"], "matched_set": o["matched_set"],
                    "mix_allowed": o["mix_allowed"], "parts_switching_together": det,
                    "max_assemblies_with_this_option": cap if cap != math.inf else 0})
    return {"primary": primary, "parent": parent, "plant": plant, "date": str(day), "product_context": product,
            "product_line": line, "options": res}


def position_capacity(assembly: str, refdes: str, plant: str, day, qty: float, include_po: bool = False) -> dict:
    """How many `assembly` units the part(s) at a reference-designator position allow: primary + usable substitutes."""
    D, day = data(), d_(day)
    want = refdes.replace(" ", "").upper()
    lines = [ln for ln in D.children.get(assembly, []) if eff(ln, day) and ln["RefDes"].replace(" ", "").upper() == want]
    out = []
    for ln in lines:
        need = ln["Qty"] * (1 + ln["Scrap"])
        s = available(ln["Child"], plant, day)
        po = in_transit(ln["Child"], plant, day)["in_transit"] if include_po else 0
        prim = math.floor((s["available"] + po) / need + 1e-9)
        sub = substitution_capacity(ln["Child"], assembly, plant, day, include_po)
        alt_total = sum(o["max_assemblies_with_this_option"] for o in sub["options"] if o.get("max_assemblies_with_this_option"))
        out.append({"part": ln["Child"], "refdes": ln["RefDes"], "qty_per_assembly": need, "primary_available": s["available"],
                    "primary_lots_excluded": s["lots_excluded"], "primary_in_transit": po, "assemblies_from_primary": prim,
                    "substitute_options": sub["options"], "assemblies_from_substitutes": alt_total,
                    "max_assemblies": min(prim + alt_total, int(qty)) if qty else prim + alt_total,
                    "max_assemblies_uncapped": prim + alt_total, "requested": qty})
    return {"assembly": assembly, "refdes": refdes, "plant": plant, "date": str(day), "positions": out,
            "note": "substitution counted in whole assemblies (MixAllowed = N: one board is all primary or all substitute)"}
