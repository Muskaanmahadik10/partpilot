from datetime import date

import pytest

from partpilot import normalize as N


@pytest.mark.parametrize("raw,expected", [
    ("ca-1001", "CA-1001"), ("ＣＡ－１００１", "CA-1001"), (" CA - 1001 ", "CA-1001"), ("ca_1001", "CA-1001"),
    ("brd-uno3", "BRD-UNO3"), ("asm-11000", "ASM-11000"),
    ("CA-1O01", None), ("ZZ-1001", None), ("CA-10011", None), ("ASM-1100", None), ("", None), (None, None),
])
def test_part_numbers_normalised_never_guessed(raw, expected):
    assert N.norm_partno(raw) == expected


def test_part_types_and_lines():
    assert N.part_type_of("BRD-PICOH") == "PRODUCT" and N.line_of_product("PRN-MK3S") == "PRN"
    assert N.part_type_of("ASM-21000") == "ASSEMBLY" and N.part_type_of("MC-1003") == "COMPONENT"
    assert N.commodity_of("MC-1003") == "MC" and N.commodity_of("ASM-21000") is None
    assert N.part_type_of(None) is None and N.line_of_product("CA-1001") is None


@pytest.mark.parametrize("raw,code", [("North Ridge", "NRG"), ("PLANT NRG", "NRG"), ("bay-haven", "BHV"), ("BayHaven", "BHV"),
                                      ("elm", "ELM"), ("HQ", None), ("", None)])
def test_plants(raw, code):
    assert N.norm_plant(raw) == code


def test_dates_only_three_formats():
    assert N.parse_date("2027-03-15") == date(2027, 3, 15)
    assert N.parse_date("2027/03/15") == date(2027, 3, 15)
    assert N.parse_date("20270315") == date(2027, 3, 15)
    assert N.parse_date("03/15/2027") is None and N.parse_date("2027-13-01") is None and N.parse_date("Q3 2026") is None


def test_numbers_and_scrap():
    assert N.to_number("1,200") == 1200 and N.to_number("abc") is None and N.to_number("inf") is None
    assert N.scrap_rate("2%") == (0.02, False) and N.scrap_rate("0.5") == (0.0, True) and N.scrap_rate("low") == (0.0, True)
    assert N.scrap_rate("") == (0.0, False)
    assert N.enum(" released ", ["RELEASED"]) == "RELEASED" and N.enum("ok", ["RELEASED"]) is None


@pytest.mark.parametrize("code,value,unit,expected", [
    ("Capacitance", "100", "nF", 0.1), ("Capacitance", "0.1", "uF", 0.1), ("Capacitance", "100nF", "", 0.1),
    ("Capacitance", "22pF", "", 2.2e-05), ("Capacitance", "47", "", 47.0), ("Resistance", "4.7k", "", 4700.0),
    ("Resistance", "1M", "", 1e6), ("Resistance", "10 ohm", "", 10.0), ("VoltageRating", "6.3", "V", 6.3),
    ("VoltageRating", "10", "Vac", None), ("VoltageRating", "TBD", "", None), ("TolerancePct", "±10%", "", 10.0),
    ("Dielectric", "np0", "", "C0G"), ("Dielectric", "X9Z", "", None), ("Package", "USON8-2X3", "", "USON82X3"),
    ("RoHS", "yes", "", "Y"), ("RoHS", "maybe", "", None), ("ShelfLifeMonths", "24 months", "", 24.0),
    ("ShelfLifeMonths", "2", "years", None), ("TempMaxC", "85 °C", "", 85.0), ("Inductance", "2.2uH", "", 2.2),
])
def test_attribute_units(code, value, unit, expected):
    got = N.norm_attr_value(code, value, unit)
    assert got == pytest.approx(expected) if isinstance(expected, float) else got == expected


def test_attribute_codes_and_dates():
    assert N.norm_attr_code("voltage_rating") == "VoltageRating" and N.norm_attr_code("Colour") is None
    assert N.iso_monday("2610") == date(2026, 3, 2) and N.iso_monday("2654") is None and N.iso_monday("26W1") is None
    assert N.iso_monday("2553") is None and N.iso_monday("2653") == date(2026, 12, 28)
    assert N.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
