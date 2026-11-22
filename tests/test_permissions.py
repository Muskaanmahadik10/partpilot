import json

import pytest

from partpilot import tools as T
from partpilot.perm import Denied, User, aggregate


def run(u, name, **args):
    return T.run(u, name, args)


def test_invalid_user_scope_rejected():
    with pytest.raises(ValueError):
        User("x", "product_owner", "XYZ")
    with pytest.raises(ValueError):
        User("x", "superuser", "ALL")


def test_product_owner_sees_only_own_line(users, pn):
    po = users["po_boards"]
    assert "permission_denied" in run(po, "bom_explode", root="PRN-MK3S")
    assert "permission_denied" in run(po, "part_info", part="MD-1001")
    r = run(po, "where_used", part=pn("ESD shielding bag 150 x 200 mm"))
    assert r["products"] == ["BRD-PICO", "BRD-PICOH", "BRD-UNO3"]          # the printer is never revealed
    assert "PRN" not in json.dumps(r)


def test_product_owner_same_denial_for_unknown_and_hidden(users):
    po = users["po_boards"]
    hidden, unknown = run(po, "part_info", part="MD-1001"), run(po, "part_info", part="MD-1999")
    assert set(hidden) == set(unknown) == {"permission_denied"}            # does not reveal whether a part exists


def test_product_owner_line_scoped_substitutes(users, pn):
    pp = users["po_printers"]
    r = run(pp, "alternates", primary=pn("LM8UU"), parent="ASM-31000")
    assert {u["substitute"] for u in r["usable"]} == {pn("Linear bearing LM8UU (generic)")}
    assert "permission_denied" in run(users["po_boards"], "alternates", primary=pn("LM8UU"))


def test_plant_lead_own_plant_only(users, pn):
    ln = users["lead_nrg"]
    assert run(ln, "inventory", part=pn("RP2040"), plant="NRG", date="2027-03-15")["available"] == 3200
    assert "permission_denied" in run(ln, "inventory", part=pn("RP2040"), plant="ELM")
    assert "permission_denied" in run(ln, "open_po", part=pn("RP2040"), plant="BHV")
    assert run(ln, "inventory", part=pn("RP2040"), plant="north ridge", date="2027-03-15")["available"] == 3200


def test_field_sensitivity(users, pn):
    rp = pn("RP2040")
    lead = run(users["lead_nrg"], "part_info", part=rp)["part"]
    assert lead["SupplierName"] and "UnitCostUSD" not in lead
    plan = run(users["planner"], "part_info", part=rp)["part"]
    assert "SupplierName" not in plan and "UnitCostUSD" not in plan and "MfrPartNo" not in plan
    eng = run(users["eng_semis"], "part_info", part=rp)["part"]
    assert eng["UnitCostUSD"] == 0.8 and eng["SupplierName"]
    assert "Remark" not in json.dumps(run(users["eng_passives"], "alternates", primary="CA-1001"))
    assert "NOTE TO THE ASSISTANT" not in json.dumps(run(users["eng_passives"], "alternates", primary="CA-1001"))


def test_aggregates_fewer_than_three_and_no_subtraction(users, pn):
    pl = users["planner"]
    assert run(pl, "supplier_price_stats", group="SW")["unit_cost_aggregate"] == {"count": "fewer than 3"}
    r = run(pl, "supplier_price_stats", group="BRD-PICO")["unit_cost_aggregate"]
    assert set(r) == {"count", "avg"} and r["count"] >= 3
    lst = run(pl, "supplier_price_stats", parts=[pn("RP2040"), "mc-1003", " MC-1003 "])
    assert isinstance(lst["unit_cost_aggregate"], str)                     # arbitrary lists: no cost stats at all
    assert aggregate([1.0, 1.0], "stats") == {"count": "fewer than 3"}
    assert aggregate(["A", "A", "B"], "distinct_count") == {"distinct_count": "fewer than 3"}
    assert "permission_denied" not in run(users["lead_nrg"], "supplier_price_stats", parts=[pn("RP2040")])
    assert "unit_cost_usd" not in run(users["lead_nrg"], "supplier_price_stats", parts=[pn("RP2040")])


def test_component_engineer_scope_guard_withholds_capacity(users):
    ep = users["eng_passives"]
    r = run(ep, "max_buildable", product="BRD-UNO3", plant="NRG", date="2027-03-15")
    assert str(r["max_units"]).startswith("withheld") and "MC-1001" not in json.dumps(r)
    s = run(ep, "shortage_simulation", product="BRD-UNO3", qty=600, plant="NRG", date="2027-03-15", include_open_po=True)
    assert all(i["component"].startswith(("CA-", "RE-", "IN-", "XT-", "PR-")) for i in s["short_items"])
    assert "scope_filtered" in s


def test_component_engineer_full_shortage_with_substitutes(users):
    r = run(users["planner"], "shortage_simulation", product="BRD-UNO3", qty=600, plant="NRG", date="2027-03-15",
            include_open_po=True, include_alternates=True)
    assert r["max_units_buildable"] == 550 and r["still_short"] == [{"component": "MC-1001", "still_short": 50}]


def test_auditor_has_no_parts_data(users):
    assert "permission_denied" in run(users["auditor"], "search_parts", query="RP2040")
    with pytest.raises(Denied):
        users["auditor"].check_part("MC-1003")


def test_argument_validation(users, pn):
    pl = users["planner"]
    assert "error" in run(pl, "inventory", part=pn("RP2040"), plant="HQ")
    assert "error" in run(pl, "bom_explode", root="BRD-UNO3", qty=-5)
    assert "error" in run(pl, "bom_explode", root="BRD-UNO3", date="15/03/2027")
    assert "error" in run(pl, "part_info", part="CA-1O01")
    assert "error" in run(pl, "nope")
    assert "error" in run(pl, "inventory", part=pn("RP2040"))                 # missing plant -> bad arguments
    assert "error" in run(pl, "supplier_price_stats", group="nonsense")


def test_exact_mpn_accepted_as_part_argument(users, pn):
    assert run(users["lead_nrg"], "inventory", part="RP2040", plant="NRG", date="2027-03-15")["available"] == 3200
    assert "permission_denied" in run(users["lead_nrg"], "inventory", part="rp2040", plant="ELM")
    assert "permission_denied" in run(users["po_boards"], "part_info", part="NF-A4x10 5V")   # scope still applies
    assert "error" in run(users["planner"], "part_info", part="LM8")                         # no fuzzy guessing


def test_search_parts_prefers_exact_mpn(users, pn):
    r = run(users["planner"], "search_parts", query="RP2040")
    assert r["matches"][0]["part"] == pn("RP2040")
    assert run(users["po_boards"], "search_parts", query="Einsy")["count"] == 0


def test_other_tools_smoke(users, pn):
    pl = users["planner"]
    assert run(pl, "open_po", part=pn("ATMEGA16U2-MU"), plant="NRG", date="2027-03-15")["in_transit_counted"] == 200
    assert run(pl, "bom_explode", root="BRD-UNO3", commodity="CA")["component_count"] > 3
    r = run(pl, "lead_time_impact", part=pn("RP2040"), new_lead_days=90, from_date="2027-03-01")
    assert r["products_using_it"] == ["BRD-PICO", "BRD-PICOH"] and "NRG" in r["supply_by_plant"]
    assert run(pl, "position_capacity", assembly="ASM-11000", refdes="Y1", plant="NRG", date="2027-03-15", qty=600)["positions"]
    assert run(pl, "substitution_capacity", primary=pn("NCP1117ST50T3G"), parent="ASM-11000", plant="NRG", date="2027-03-15")["options"]
    assert "permission_denied" in run(pl, "pending_review")
    assert run(users["eng_semis"], "pending_review")["count"] >= 1
    assert T.call(pl, "part_info", {"part": "BRD-UNO3"}).startswith("{")
