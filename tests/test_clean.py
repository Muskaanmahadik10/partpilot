import json
import os

from conftest import CLEAN_RESULT


def report():
    with open(os.environ["PARTPILOT_REPORT"], encoding="utf-8") as f:
        return f.read()


def test_counts_and_pending():
    c = CLEAN_RESULT["counts"]
    assert c["parts_master"][1] < c["parts_master"][0] and c["bom"][1] < c["bom"][0]
    with open(os.path.join(os.environ["PARTPILOT_CLEAN_DIR"], "pending_review.json"), encoding="utf-8") as f:
        kinds = {p["kind"] for p in json.load(f)}
    assert {"PART_MASTER_CONFLICT", "ATTRIBUTE_CONFLICT", "BOM_LINE_CONFLICT", "BOM_CYCLE", "ALTERNATE_CONFLICT",
            "ALTERNATE_PENDING"} <= kinds


def test_report_lists_planted_problems():
    r = report()
    for text in ("Invalid part number", "UoM 'EA'", "Scrap rate given as a percentage", "circular reference",
                 "Plant written as an alias", "Lots counted in KPCS", "ETA unreadable", "SUB-0003",
                 "Unknown attribute code", "EffectiveTo earlier than EffectiveFrom", "received > ordered"):
        assert text in r, text


def test_invalid_part_numbers_are_dropped_not_guessed(D):
    assert "CA-1O99" not in D.parts and "ZZ-1001" not in D.parts and "CA-1099" not in D.parts


def test_messy_part_numbers_recovered(D, pn):
    assert pn("RC0402FR-0710KL").startswith("RE-") and pn("LP2985-33DBVR") in D.parts and pn("FDN340P") in D.parts
    assert D.parts[pn("ATMEGA328P-PU")]["PartType"] == "COMPONENT"      # type taken from the part number
    assert D.parts["BRD-PICOH"]["ProductLine"] == "BRD"                   # line taken from the part number


def test_unit_conversions(D, pn):
    lines = {(ln["Parent"], ln["Child"]): ln for ln in D.bom}
    assert lines[("ASM-21000", pn("GRM033R60J104KE19D"))]["Qty"] == 9          # 0.009 KPCS
    assert lines[("ASM-32000", pn("GT2-6MM"))]["Qty"] == 0.85                  # 85 CM
    assert lines[("ASM-34000", pn("PTFE tube 4 x 2 mm"))]["Qty"] == 0.05       # 50 MM
    assert lines[("ASM-11000", pn("GRM188R71H104KA93D"))]["Scrap"] == 0.01     # "1%"
    assert lines[("ASM-11000", pn("ATMEGA328P-PU"))]["Scrap"] == 0.0           # 0.5 is out of range -> 0


def test_conflicts_and_cycles_not_used(D):
    assert not any("ASM-36900" in (ln["Parent"], ln["Child"]) for ln in D.bom)
    assert [ln for ln in D.bom if ln["Parent"] == "BRD-PICOH" and ln["RefDes"] == "DBG"] == []
    assert not any(r["MfrPartNo"] == "GRM188R61A106KE69D" for r in D.parts.values())  # conflicting master records


def test_remark_kept_for_humans_only(D):
    rec = next(a for a in D.alts if a["SubID"] == "SUB-0003")
    assert "NOTE TO THE ASSISTANT" in rec["Remark_HUMAN_ONLY"] and rec["Status"] == "Pending"


def test_generator_is_deterministic():
    import generate
    a, _ = generate.build(42)
    b, _ = generate.build(42)
    assert a == b
