import json

from partpilot import engine as E


def test_explode_multi_level_and_units(pn):
    ex = E.explode("PRN-MK3S", 10, "2027-03-15")
    assert ex["gross"][pn("GT2-6MM")] == 15.3                      # (0.85 + 0.65) m x 1.02 scrap x 10
    assert ex["gross"][pn("Heater block V6")] == 10                 # three levels: product > extruder > hotend
    assert not ex["assemblies_without_bom"]


def test_engineering_change_by_date(pn):
    before = [p["component"] for p in E.explode("ASM-11000", 1, "2027-03-31")["positions"] if p["refdes"] == "ON"]
    after = [p["component"] for p in E.explode("ASM-11000", 1, "2027-04-01")["positions"] if p["refdes"] == "ON"]
    assert before == [pn("APT2012SGC")] and after == [pn("APT2012LZGCK")]


def test_where_used(pn):
    assert E.where_used("ASM-21000")["products"] == ["BRD-PICO", "BRD-PICOH"]
    assert "PRN-MK3S" in E.where_used(pn("ESD shielding bag 150 x 200 mm"))["products"]
    assert E.where_used(pn("APT2012LZGCK"), "2027-03-15")["direct_parents"] == []


def test_available_rules(pn):
    a = E.available(pn("RP2040"), "NRG", "2027-03-15")
    assert a["available"] == 3200 and a["lots_excluded"] == {"QC HOLD": 1, "QC invalid value": 1}   # KPCS lot = 1000
    c = E.available(pn("EEE-FK1E470P"), "NRG", "2027-03-15")
    assert c["available"] == 900 and c["lots_excluded"]["expired before the production date"] == 1
    assert c["lots_excluded"]["invalid date code on a shelf-life part"] == 1


def test_in_transit_inspection_days(pn):
    t = E.in_transit(pn("ATMEGA16U2-MU"), "NRG", "2027-03-15")
    assert t["in_transit"] == 200 and t["lines_excluded"]["Closed"] == 1
    assert E.in_transit(pn("ATMEGA16U2-MU"), "NRG", "2027-03-16")["in_transit"] == 350


def test_max_buildable_scenarios():
    m = E.max_buildable("BRD-UNO3", "NRG", "2027-03-15", include_po=True, use_alternates=True)
    assert m["max_units"] == 550 and m["limiting_parts"] == ["MC-1001"]
    assert E.max_buildable("BRD-UNO3", "NRG", "2027-03-15", include_po=False, use_alternates=False)["max_units"] == 350
    assert E.max_buildable("BRD-UNO3", "NRG", "2027-03-15", include_po=True, use_alternates=False)["max_units"] == 400
    p = E.max_buildable("PRN-MK3S", "BHV", "2027-03-20")
    assert p["max_units"] == 110 and p["limiting_parts"] == ["MD-1001"]


def test_matched_set_switches_together(pn):
    m = E.max_buildable("BRD-UNO3", "NRG", "2027-03-15")
    xt = [x for x in m["substitution_plan"] if x["primary"] == pn("ABLS-16.000MHZ-B4-T")][0]
    assert {s[1] for s in xt["substitutes"]} == {pn("ECS-160-20-5PX-TR"), pn("GRM1885C1H270JA01D")}
    assert xt["assemblies_switched"] == 100


def test_shortage_with_and_without_po(pn):
    s = E.shortage("PRN-MK3S", 120, "BHV", "2027-03-20", include_po=False)
    assert {i["component"]: i["short"] for i in s["short_items"]} == {"MD-1001": 40, pn("GT2-6MM"): 23.6}
    s = E.shortage("PRN-MK3S", 120, "BHV", "2027-03-20", include_po=True)
    assert {i["component"]: i["short"] for i in s["short_items"]} == {"MD-1001": 10}
    r = E.shortage_after_alternates("BRD-UNO3", 600, "NRG", "2027-03-15", include_po=True)
    still = {i["component"]: i["still_short"] for i in r["items"]}
    assert still["MC-1001"] == 50 and still[pn("NCP1117ST50T3G")] == 0


def _opts(pn, mpn, parent="ASM-11000", product="BRD-UNO3", day="2027-03-15"):
    o = E.alternate_options(pn(mpn), parent, product, day)
    return {u["substitute"] for u in o["usable"]}, {r["substitute"]: r["rule"] for r in o["rejected"]}


def test_substitute_rules(pn):
    ok, bad = _opts(pn, "NCP1117ST50T3G")
    assert ok == {pn("NCV1117ST50T3G")} and "VoltageRating lower" in bad[pn("LM1117MPX-5.0/NOPB")]
    ok, bad = _opts(pn, "GRM188R71H104KA93D")
    assert ok == {pn("CL10B104KB8NNNC")} and bad[pn("C0603C104K5RACTU")].startswith("approval: status Pending")
    assert pn("CC0603KRX7R9BB104") not in ok                                  # no chaining
    ok, bad = _opts(pn, "CC0603KRX7R9BB104")
    assert "direction" in bad[pn("CL10B104KB8NNNC")]                            # one-way record
    ok, bad = _opts(pn, "FDN340P")
    assert "lifecycle" in bad[pn("NDS352AP")]
    ok, bad = _opts(pn, "APT2012SGC")
    assert "validity" in bad[pn("150080VS75000")]
    ok, bad = _opts(pn, "RC0603FR-071ML")
    assert "scope: LINE PRN" in bad[pn("ERJ-3EKF1004V")]
    ok, bad = _opts(pn, "W25Q16JVUXIQ", "ASM-21000", "BRD-PICO")
    assert "Package differs" in bad[pn("W25Q16JVSSIQ")]
    ok, bad = _opts(pn, "GRM21BR60J476ME01L", "ASM-21000", "BRD-PICO")
    assert ok == {pn("CL21A476MQYNNNE"), pn("GRM21BR61A226ME51L")}             # ratio 2 skips the value check
    ok, bad = _opts(pn, "GRM21BR60J476ME01L", "ASM-11000", "BRD-UNO3")
    assert "scope: ASSEMBLY" in bad[pn("GRM21BR61A226ME51L")]


def test_two_way_record_reverse_direction(pn):
    ok, _ = _opts(pn, "CL10B104KB8NNNC")
    assert pn("GRM188R71H104KA93D") in ok


def test_position_and_substitution_capacity(pn):
    p = E.position_capacity("ASM-11000", "Y1", "NRG", "2027-03-15", 1000)
    pos = p["positions"][0]
    assert pos["assemblies_from_primary"] == 450 and pos["assemblies_from_substitutes"] == 200 and pos["max_assemblies"] == 650
    s = E.substitution_capacity(pn("ABLS-16.000MHZ-B4-T"), "ASM-11000", "NRG", "2027-03-15")
    assert s["options"][0]["max_assemblies_with_this_option"] == 200 and s["product_line"] == "BRD"


def test_pending_approval_makes_substitute_usable(pn, state_dir):
    ok, _ = _opts(pn, "W25Q16JVUXIQ", "ASM-21000", "BRD-PICO")
    assert ok == set()
    (state_dir / "approvals.json").write_text(json.dumps({"SUB-0017": {"decision": "approve"}}))
    ok, _ = _opts(pn, "W25Q16JVUXIQ", "ASM-21000", "BRD-PICO")
    assert ok == {pn("GD25Q16CEIGR")}
