"""Offline mode: a deterministic keyword router that answers without any LLM.

It parses part numbers / MPNs, plant, date and quantity from the question, picks one intent by keywords and calls the
same permission-checked tools the LLM agent uses, then fills a fixed answer template. It is the demo mode when no
model is configured and the "rules baseline" in the eval. It handles one intent per question and no follow-ups."""
from __future__ import annotations

import re

from . import tools as T
from .normalize import COMMODITIES, PART_ANY, PLANTS, norm_partno, norm_plant, part_type_of
from .perm import User

T_SEND = re.compile(r"(send|e-?mail|mail|forward|share|export|upload)\b.{0,120}(external|outside|distributor|supplier|vendor|customer|partner|@)", re.I | re.S)

PRODUCT_WORDS = [(r"\bpico\s*h\b", "BRD-PICOH"), (r"\bpico\b", "BRD-PICO"), (r"\buno\b", "BRD-UNO3"),
                 (r"\b(mk3s\+?|printer|printers)\b", "PRN-MK3S")]
PLANT_WORDS = {r"north\s*ridge": "NRG", r"bay\s*haven": "BHV", r"elmstead": "ELM"}
STOP = {"BOARD", "BOARDS", "UNITS", "PRINTER", "PRINTERS", "STOCK", "PLANT", "PRICE", "COST", "SUPPLIER", "WHICH", "WHERE",
        "BUILD", "SHORT", "MAX", "USING", "WITH", "FROM", "THAT", "WHAT", "WHEN", "HAVE", "OPEN", "ORDER", "ORDERS", "INSTEAD",
        "SUBSTITUTE", "ALTERNATE", "REPLACE", "AVAILABLE", "INVENTORY", "TODAY", "NORTH", "RIDGE", "HAVEN", "BAY"}


def _run(user, calls, name, args):
    res = T.run(user, name, args)
    calls.append({"tool": name, "args": args, "denied": "permission_denied" in res})
    return res


def parse(user: User, q: str, calls: list) -> dict:
    up = q.upper()
    found = [(m.start(), norm_partno(m.group(1))) for m in PART_ANY.finditer(up)]
    found = [(i, p) for i, p in found if p]
    if not any(part_type_of(p) == "COMPONENT" for _, p in found):  # real-world names / MPNs -> internal numbers
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-./]{3,}", q):
            tok = m.group(0)
            if tok.upper() in STOP or not re.search(r"\d", tok) or norm_partno(tok) or len(found) >= 3:
                continue
            r = _run(user, calls, "search_parts", {"query": tok})
            hit = (r.get("matches") or [None])[0]
            if hit:
                found.append((m.start(), hit["part"]))
    found.sort()
    parts = list(dict.fromkeys(p for _, p in found))
    pos = {p: i for i, p in reversed(found)}
    product = next((p for p in parts if p[:3] in ("BRD", "PRN")), None)
    if not product:
        for rx, pn in PRODUCT_WORDS:
            if re.search(rx, q, re.I):
                product = pn
                break
    plant = None
    for tok in re.findall(r"\b[A-Za-z]{3}\b", q):
        if tok.upper() in PLANTS:
            plant = tok.upper()
    for rx, code in PLANT_WORDS.items():
        if re.search(rx, q, re.I):
            plant = code
    dm = re.search(r"\b(20\d\d-\d\d-\d\d)\b", q)
    stripped = re.sub(r"20\d\d-\d\d-\d\d|[A-Z]{2,3}-[A-Z0-9]{3,6}", " ", up)
    qm = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:X\b|PCS|UNITS?|BOARDS?|PRINTERS?|KITS?|PIECES)?", stripped)
    comm = next((c for c in re.findall(r"\b([A-Z]{2})\b", up) if c in COMMODITIES), None)
    kw = re.search(r"\b(replace|instead of|substitute for|alternate for|alternative to)\b", q, re.I)
    primary = None
    comps = [p for p in parts if part_type_of(p) == "COMPONENT"]
    if kw and len(comps) >= 2:
        after = [p for p in comps if pos[p] > kw.start()]
        primary = after[0] if after else comps[-1]
    return {"parts": parts, "primary": primary, "product": product, "plant": plant or (norm_plant(user.scope) if user.role == "plant_lead" else None),
            "date": dm.group(1) if dm else "2027-03-01", "qty": float(qm.group(1)) if qm else None, "commodity": comm}


def _err(res):
    if "permission_denied" in res:
        return "Not available to your role: " + res["permission_denied"]
    if "error" in res:
        return "Could not answer: " + res["error"]
    return None


def answer(user: User, q: str):
    calls = []
    low = q.lower()
    P = parse(user, q, calls)
    comp = next((p for p in P["parts"] if part_type_of(p) == "COMPONENT"), None)
    target = comp or P["product"]
    basis = f"\n\nBasis: production date {P['date']}" + (f", plant {P['plant']}" if P["plant"] else "") + "; offline rules mode (no LLM)."

    if T_SEND.search(q):
        msg = "I cannot send, e-mail or share data outside the company; PartPilot has no such function."
        if P["product"] and P["plant"] and P["qty"]:
            res = _run(user, calls, "shortage_simulation", {"product": P["product"], "qty": P["qty"], "plant": P["plant"],
                                                             "date": P["date"], "include_open_po": True})
            if not _err(res):
                rows = ", ".join(f"{r['component']} short {r['short']:g}" for r in res["short_items"]) or "nothing short"
                msg += (f"\nInternal view for you only (no prices): {P['product']} x {P['qty']:g} at {P['plant']} on "
                        f"{P['date']} incl. open POs: {rows}.")
        return msg + basis, calls

    if re.search(r"substitut|alternat|replace|instead|second source", low) and comp:
        cand = None
        if P["primary"]:
            cand = next(p for p in P["parts"] if part_type_of(p) == "COMPONENT" and p != P["primary"])
            comp = P["primary"]
        res = _run(user, calls, "alternates", {"primary": comp, "date": P["date"]})
        e = _err(res)
        if e:
            return e + basis, calls
        u = "\n".join(f"- {x['substitute']} (record {x['record']}, scope {x['scope']}, ratio {x['ratio']})" for x in res["usable"]) or "- none"
        r = "\n".join(f"- {x.get('substitute')} (record {x.get('record')}): {x['rule']}" for x in res["rejected"]) or "- none"
        head = ""
        if cand:
            ok = any(x["substitute"] == cand for x in res["usable"])
            why = next((x["rule"] for x in res["rejected"] if x.get("substitute") == cand),
                       "there is no direct substitute record (records are never chained)")
            head = f"Yes: {cand} is a usable substitute for {comp}.\n" if ok else f"No: {cand} cannot replace {comp} - {why}.\n"
        return head + f"Usable substitutes for {comp}:\n{u}\nNot usable:\n{r}" + basis, calls

    if re.search(r"\b(price|cost|supplier|vendor)s?\b", low):
        args = {"group": P["product"]} if P["product"] and not comp else ({"group": P["commodity"]} if P["commodity"] else {"parts": [comp] if comp else []})
        res = _run(user, calls, "supplier_price_stats", args)
        e = _err(res)
        if e:
            return e + basis, calls
        lines = [f"Supplier / cost information for {args.get('group') or ', '.join(args.get('parts') or [])} ({res['parts']} parts):"]
        for k in ("suppliers", "suppliers_aggregate", "unit_cost_usd", "unit_cost_aggregate", "note_supplier", "note_price"):
            if k in res:
                lines.append(f"- {k}: {res[k]}")
        return "\n".join(lines) + basis, calls

    if re.search(r"how many .*(build|make)|max(imum)? .*build|buildable|can we build", low) and P["product"]:
        if not P["plant"]:
            return "Please name the plant (NRG, BHV or ELM)." + basis, calls
        res = _run(user, calls, "max_buildable", {"product": P["product"], "plant": P["plant"], "date": P["date"],
                                                   "include_open_po": not re.search(r"without (open )?(po|purchase)|stock only", low),
                                                   "use_alternates": not re.search(r"without (any )?(substitutes|alternates)|stock only", low)})
        e = _err(res)
        if e:
            return e + basis, calls
        return (f"Maximum buildable {P['product']} at {P['plant']} on {P['date']}: {res['max_units']} units.\n"
                f"Limiting part(s): {', '.join(res['limiting_parts']) or 'none'}.\n"
                f"Open POs counted: {res['include_po']}; legal substitutes allowed: {res['use_alternates']}.\n"
                + "".join(f"- {x['primary']}: {x['assemblies_switched']} x {x['parent']} switched to "
                          + ", ".join(f"{s[1]} (record {s[2]})" for s in x["substitutes"]) + "\n"
                          for x in res.get("substitution_plan", []))
                + basis), calls

    if re.search(r"short|shortage|missing", low) and P["product"]:
        if not (P["plant"] and P["qty"]):
            return "Please give quantity and plant for the shortage check." + basis, calls
        res = _run(user, calls, "shortage_simulation", {"product": P["product"], "qty": P["qty"], "plant": P["plant"], "date": P["date"],
                                                         "include_open_po": bool(re.search(r"\bpos?\b|purchase|incoming|in transit", low))})
        e = _err(res)
        if e:
            return e + basis, calls
        rows = res["short_items"]
        txt = "\n".join(f"- {r['component']} {r['description']}: need {r['gross']} {r['uom']}, available {r['available']}"
                        + (f", in transit {r['in_transit']}" if r["in_transit"] is not None else "") + f", short {r['short']}" for r in rows)
        return (f"{P['product']} x {P['qty']:g} at {P['plant']} on {P['date']}: {len(rows)} short component(s).\n" + (txt or "- none")
                + basis), calls

    if re.search(r"where.*used|used (in|by)\b|which products", low) and target:
        res = _run(user, calls, "where_used", {"part": target})
        e = _err(res)
        if e:
            return e + basis, calls
        return (f"{target} is used directly in {', '.join(res['direct_parents']) or 'nothing in your scope'}; products: "
                f"{', '.join(res['products']) or 'none in your scope'}." + basis), calls

    if re.search(r"\b(po|pos|purchase order|in transit|arriv|incoming)", low) and comp:
        if not P["plant"]:
            return "Please name the plant (NRG, BHV or ELM)." + basis, calls
        res = _run(user, calls, "open_po", {"part": comp, "plant": P["plant"], "date": P["date"]})
        e = _err(res)
        if e:
            return e + basis, calls
        return (f"In transit for {comp} at {P['plant']} counted by {P['date']}: {res['in_transit_counted']}. "
                f"Lines excluded: {res['lines_excluded'] or 'none'}." + basis), calls

    if re.search(r"stock|inventory|available|on hand", low) and comp:
        if not P["plant"]:
            return "Please name the plant (NRG, BHV or ELM)." + basis, calls
        res = _run(user, calls, "inventory", {"part": comp, "plant": P["plant"], "date": P["date"]})
        e = _err(res)
        if e:
            return e + basis, calls
        return (f"Available {comp} at {P['plant']} on {P['date']}: {res['available']:g} ({res['lots_counted']} lots counted). "
                f"Excluded lots: {res['lots_excluded'] or 'none'}." + basis), calls

    if re.search(r"\bbom\b|explode|how much|how many .*need|components", low) and P["product"]:
        res = _run(user, calls, "bom_explode", {"root": P["product"], "qty": P["qty"] or 1, "date": P["date"],
                                                 **({"commodity": P["commodity"]} if P["commodity"] else {})})
        e = _err(res)
        if e:
            return e + basis, calls
        rows = "\n".join(f"- {i['component']} {i['description']}: {i['gross']} {i['uom']}" for i in res["items"][:25])
        return f"BOM of {P['product']} x {P['qty'] or 1:g} ({res['component_count']} components):\n{rows}" + basis, calls

    if target:
        res = _run(user, calls, "part_info", {"part": target})
        e = _err(res)
        if e:
            return e + basis, calls
        return f"{target}: {res['part']}\nAttributes: {res['attributes']}" + basis, calls
    return "I could not identify a part or product in the question. Name a part number, MPN or product.", calls
