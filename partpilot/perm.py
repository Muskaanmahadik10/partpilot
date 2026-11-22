"""Roles, data scope and field sensitivity. Enforced in Python BEFORE any data reaches the LLM:
the tools only ever hand the model results that were already filtered for the logged-in user.

Field sensitivity: general (most fields) | sensitive (SupplierName, MfrPartNo, Manufacturer) | restricted (UnitCostUSD, UnitPriceUSD)

  planner             Supply Planner      whole company; general = detail, sensitive and restricted = aggregate only
  product_owner       Product Owner       own product line (BRD or PRN) incl. direct substitutes; sensitive = aggregate,
                                          restricted = none; must not learn anything about the other line
  plant_lead          Plant Materials Lead stock and POs of the own plant only (master/BOM/substitutes company-wide);
                                          general + sensitive = detail, restricted = none
  component_engineer  Component Engineer  own commodity group + every assembly/product that uses it; everything in detail;
                                          approves pending substitutes and review items of the own commodities
  auditor             Auditor             no parts data; reads audit metadata and runs the cleaning job

Aggregates over fewer than 3 records are answered as "fewer than 3".
"""
from __future__ import annotations

from functools import lru_cache

from . import engine as E
from .normalize import COMMODITY_GROUPS, PLANTS, PRODUCT_LINES, line_of_product, part_type_of

SENSITIVE = {"SupplierName", "MfrPartNo", "Manufacturer"}
RESTRICTED = {"UnitCostUSD", "UnitPriceUSD"}
ROLES = {"planner": "Supply Planner", "product_owner": "Product Owner", "plant_lead": "Plant Materials Lead",
         "component_engineer": "Component Engineer", "auditor": "Auditor"}
VALID_SCOPES = {"planner": {"ALL"}, "product_owner": set(PRODUCT_LINES), "plant_lead": set(PLANTS),
                "component_engineer": set(COMMODITY_GROUPS), "auditor": {"SYSTEM"}}


class Denied(Exception):
    pass


def _bom_closure(roots):
    D = E.data()
    seen, stack = set(roots), list(roots)
    while stack:
        for ln in D.children.get(stack.pop(), []):
            if ln["Child"] not in seen:
                seen.add(ln["Child"])
                stack.append(ln["Child"])
    return seen


@lru_cache(maxsize=8)
def line_bom(line: str) -> frozenset:
    """Products of a line and everything in their BOMs (no substitutes)."""
    D = E.data()
    return frozenset(_bom_closure([p for p in D.parts if part_type_of(p) == "PRODUCT" and line_of_product(p) == line]))


@lru_cache(maxsize=8)
def line_scope(line: str) -> frozenset:
    """BOM parts of the line plus DIRECT substitutes of those parts (never a second hop)."""
    D = E.data()
    bom = line_bom(line)
    extra = set()
    for a in D.alts:
        if a["Primary"] in bom:
            extra.add(a["Alt"])
        elif a.get("TwoWay") == "Y" and a["Alt"] in bom:
            extra.add(a["Primary"])
    return frozenset(bom | extra)


@lru_cache(maxsize=8)
def commodity_scope(group: str) -> frozenset:
    """Components of the assigned commodities plus every assembly/product above them."""
    D = E.data()
    coms = set(COMMODITY_GROUPS[group])
    comps = {p for p, r in D.parts.items() if r["Commodity"] in coms}
    seen, stack = set(comps), list(comps)
    while stack:
        for ln in D.parents.get(stack.pop(), []):
            if ln["Parent"] not in seen:
                seen.add(ln["Parent"])
                stack.append(ln["Parent"])
    return frozenset(seen)


def clear_caches():
    line_bom.cache_clear()
    line_scope.cache_clear()
    commodity_scope.cache_clear()


class User:
    def __init__(self, username, role, scope):
        if role not in ROLES:
            raise ValueError(f"unknown role {role}")
        if scope not in VALID_SCOPES[role]:
            raise ValueError(f"scope {scope!r} is not valid for role {role} (valid: {sorted(VALID_SCOPES[role])})")
        self.username, self.role, self.scope = username, role, scope

    @property
    def role_name(self):
        return ROLES[self.role]

    # ---- data scope ----
    def check_part(self, part: str):
        if self.role == "auditor":
            raise Denied("The Auditor role cannot see any parts data.")
        if self.role == "product_owner" and part not in line_scope(self.scope):
            raise Denied(f"{part} is outside your data scope (product line {self.scope}).")
        if self.role == "component_engineer" and part not in commodity_scope(self.scope):
            raise Denied(f"{part} is outside your data scope (commodity group {self.scope}).")

    def check_plant(self, plant: str):
        if self.role == "plant_lead" and plant != self.scope:
            raise Denied(f"You can only see stock and purchase orders of your own plant {self.scope}.")

    def can_approve(self, commodity: str | None) -> bool:
        return self.role == "component_engineer" and commodity in COMMODITY_GROUPS[self.scope]

    # ---- field sensitivity ----
    def sensitive_level(self):
        return {"planner": "aggregate", "product_owner": "aggregate", "plant_lead": "detail",
                "component_engineer": "detail"}.get(self.role, "none")

    def price_level(self):
        return {"planner": "aggregate", "component_engineer": "detail"}.get(self.role, "none")

    def filter_record(self, rec: dict) -> dict:
        out = {}
        for k, v in rec.items():
            if k in RESTRICTED and self.price_level() != "detail":
                continue
            if k in SENSITIVE and self.sensitive_level() != "detail":
                continue
            if "Remark" in k:
                continue  # free text never leaves Python (prompt-injection guard)
            out[k] = v
        return out


def aggregate(values: list, kind: str) -> dict:
    """Aggregate with the fewer-than-3 rule. min/max are never returned (they would expose one part's price)."""
    vals = [v for v in values if v is not None]
    if kind == "distinct_count":
        n = len(set(vals))
        return {"distinct_count": n if n >= 3 else "fewer than 3"}
    if len(vals) < 3:
        return {"count": "fewer than 3"}
    return {"count": len(vals), "avg": round(sum(vals) / len(vals), 4)}
