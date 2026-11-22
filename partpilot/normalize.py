"""Value normalisation shared by the cleaner, the tools and the offline router.

Part-number scheme (PartPilot's own):
  product    BRD-UNO3, BRD-PICO, PRN-MK3S   prefix = product line (BRD = dev boards, PRN = 3D printers)
  assembly   ASM-12345                        five digits
  component  CA-0101                          two-letter commodity code + four digits
Invalid part numbers are never "repaired" by guessing (a letter O is not turned into a zero).
"""
from __future__ import annotations

import calendar
import math
import re
import unicodedata
from datetime import date, datetime

PLANTS = {"NRG": "North Ridge (board SMT line)", "BHV": "Bay Haven (printer final assembly)",
          "ELM": "Elmstead (service and spares hub)"}
PLANT_ALIASES = {"NRG": "NRG", "NORTHRIDGE": "NRG", "NORTH-RIDGE": "NRG", "N-RIDGE": "NRG",
                 "BHV": "BHV", "BAYHAVEN": "BHV", "BAY-HAVEN": "BHV",
                 "ELM": "ELM", "ELMSTEAD": "ELM"}

PRODUCT_LINES = {"BRD": "Dev boards", "PRN": "3D printers"}

COMMODITY_GROUPS = {
    "Passives": ["CA", "RE", "IN", "XT", "PR"],
    "Semiconductors": ["DI", "TR", "IC", "MC", "LD"],
    "Electromechanical": ["CN", "SW", "MO", "FN", "SN", "HT", "MD", "CB"],
    "Mechanical": ["PC", "ME", "PT", "FR", "PK"],
}
COMMODITY_NAMES = {"CA": "capacitors", "RE": "resistors", "IN": "inductors and ferrites", "XT": "crystals and resonators",
                   "PR": "circuit protection", "DI": "diodes", "TR": "transistors", "IC": "analog and memory ICs",
                   "MC": "microcontrollers", "LD": "LEDs", "CN": "connectors", "SW": "switches", "MO": "motors",
                   "FN": "fans", "SN": "sensors", "HT": "heating and hotend parts", "MD": "bought modules",
                   "CB": "cables and sleeves", "PC": "bare PCBs", "ME": "mechanical hardware", "PT": "printed parts",
                   "FR": "frame parts", "PK": "packaging"}
COMMODITIES = {c for v in COMMODITY_GROUPS.values() for c in v}
GROUP_OF = {c: g for g, cs in COMMODITY_GROUPS.items() for c in cs}

RE_PRODUCT = re.compile(r"^(BRD|PRN)-[A-Z0-9]{3,6}$")
RE_ASM = re.compile(r"^ASM-\d{5}$")
RE_CMP = re.compile(r"^([A-Z]{2})-\d{4}$")
# used to find part numbers inside free text / tool output
PART_ANY = re.compile(r"(?<![A-Z0-9-])((?:BRD|PRN)-[A-Z0-9]{3,6}|ASM-\d{5}|[A-Z]{2}-\d{4})(?![A-Z0-9])")
DASHES = "–—－−‐‑‒―_"


def is_blank(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == ""


def norm_partno(v):
    """Trim, full-width -> half-width, upper case, any dash/underscore -> '-', inner spaces removed.
    Returns None for anything that does not match the scheme."""
    if is_blank(v):
        return None
    s = unicodedata.normalize("NFKC", str(v)).strip().upper()
    for d in DASHES:
        s = s.replace(d, "-")
    s = re.sub(r"\s+", "", s)
    if RE_PRODUCT.match(s) or RE_ASM.match(s):
        return s
    m = RE_CMP.match(s)
    if m and m.group(1) in COMMODITIES:
        return s
    return None


def part_type_of(p):
    if not p:
        return None
    if RE_PRODUCT.match(p):
        return "PRODUCT"
    if RE_ASM.match(p):
        return "ASSEMBLY"
    return "COMPONENT"


def commodity_of(p):
    return p[:2] if part_type_of(p) == "COMPONENT" else None


def line_of_product(p):
    return p[:3] if p and RE_PRODUCT.match(p) else None


def norm_plant(v):
    if is_blank(v):
        return None
    s = unicodedata.normalize("NFKC", str(v)).strip().upper()
    for d in DASHES:
        s = s.replace(d, "-")
    s = re.sub(r"^PLANT[\s-]*", "", s).replace(" ", "")
    return PLANT_ALIASES.get(s)


def parse_date(v):
    """Accepted: YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD. Anything else (e.g. 03/15/2027, 2027-13-01) -> None."""
    if is_blank(v):
        return None
    s = str(v).strip()
    for fmt, rx in (("%Y-%m-%d", r"^\d{4}-\d{2}-\d{2}$"), ("%Y/%m/%d", r"^\d{4}/\d{2}/\d{2}$"), ("%Y%m%d", r"^\d{8}$")):
        if re.match(rx, s):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                return None
    return None


def to_number(v):
    if is_blank(v):
        return None
    s = unicodedata.normalize("NFKC", str(v)).strip().replace(",", "").replace("−", "-")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def enum(v, allowed, case_insensitive=True):
    if is_blank(v):
        return None
    s = str(v).strip()
    for a in allowed:
        if (s.lower() == a.lower()) if case_insensitive else (s == a):
            return a
    return None


def scrap_rate(v):
    """'2%' or 0.02 -> 0.02. Unreadable or outside 0..0.15 -> (0.0, flagged)."""
    if is_blank(v):
        return 0.0, False
    s = unicodedata.normalize("NFKC", str(v)).strip()
    try:
        x = float(s[:-1].strip()) / 100 if s.endswith("%") else float(s)
    except ValueError:
        return 0.0, True
    if not (0 <= x <= 0.15):
        return 0.0, True
    return x, False


# ---------- attributes ----------
SI = {"p": 1e-12, "n": 1e-9, "µ": 1e-6, "μ": 1e-6, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "": 1.0}
ATTR_CODES = {a.lower(): a for a in ["Capacitance", "Resistance", "Inductance", "VoltageRating", "CurrentRating", "TolerancePct",
                                       "Dielectric", "OutputVoltage", "Package", "RoHS", "ShelfLifeMonths", "TempMaxC"]}
UNIT_OF = {"Capacitance": "F", "Resistance": "Ω", "Inductance": "H", "VoltageRating": "V", "CurrentRating": "A",
           "OutputVoltage": "V"}
STD_SCALE = {"Capacitance": 1e-6, "Inductance": 1e-6}  # stored in µF and µH
DIELECTRIC_RANK = {"C0G": 3, "NP0": 3, "X7R": 2, "X5R": 1, "Y5V": 0}


def norm_attr_code(v):
    return None if is_blank(v) else ATTR_CODES.get(re.sub(r"[\s_]", "", str(v)).lower())


def _txt(s):
    s = unicodedata.normalize("NFKC", str(s)).strip()
    s = s.replace("−", "-").replace("℃", "°C").replace("Ω", "Ω")
    return re.sub(r"(?i)ohms?", "Ω", s)


def norm_attr_value(code, value, unit=""):
    """Value in the standard unit, or None when it cannot be converted (treated as missing, never estimated)."""
    if is_blank(value):
        return None
    raw = _txt(value)
    u = "" if is_blank(unit) else _txt(unit)
    if raw.upper() in ("TBD", "N/A", "NA", "-", "?"):
        return None
    if code == "Package":
        return re.sub(r"[\s\-]", "", raw).upper() or None
    if code == "RoHS":
        r = raw.upper()
        return {"YES": "Y", "NO": "N"}.get(r, r) if r in ("Y", "N", "YES", "NO") else None
    if code == "Dielectric":
        r = raw.upper().replace("NP0", "C0G")
        return r if r in DIELECTRIC_RANK else None
    if code == "TolerancePct":
        s = (raw + u).replace("±", "").replace("+/-", "").replace(" ", "")
        n = to_number(s[:-1] if s.endswith("%") else s)
        return abs(n) if n is not None else None
    if code == "ShelfLifeMonths":
        s = re.sub(r"(?i)(months?|mo)$", "", raw.replace(" ", ""))
        if u and u.lower() not in ("month", "months", "mo"):
            return None
        return to_number(s)
    if code == "TempMaxC":
        return to_number(re.sub(r"°?C$", "", (raw + u).replace(" ", "")))
    if code in UNIT_OF:
        base = UNIT_OF[code]
        s = (raw + u).replace(" ", "")
        m = re.match(r"^([+-]?\d+(?:\.\d+)?)([pnµμumkKM]?)(.*)$", s)
        if not m:
            return None
        num, prefix, rest = float(m.group(1)), m.group(2), m.group(3)
        if code == "Resistance":
            if rest not in ("", "Ω", "R"):
                return None
        elif rest not in ("", base):
            return None
        if code in STD_SCALE and prefix == "" and rest == "":
            return num  # bare number = standard unit (µF / µH)
        if code == "Resistance" and prefix == "M" and rest == "":
            pass  # 1M = 1 megaohm
        val = num * SI[prefix]
        if code in STD_SCALE:
            val = val / STD_SCALE[code]
        return round(val, 9)
    return raw


def iso_monday(datecode):
    """Date code YYWW -> Monday of that ISO week; None if invalid (including a week 53 that does not exist)."""
    if is_blank(datecode):
        return None
    s = str(datecode).strip()
    if not re.match(r"^\d{4}$", s):
        return None
    yy, ww = int(s[:2]), int(s[2:])
    if not 1 <= ww <= 53:
        return None
    try:
        return date.fromisocalendar(2000 + yy, ww, 1)
    except ValueError:
        return None


def add_months(d, months):
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))
