"""Test setup: generate the seeded raw data and clean it into a temporary folder before any partpilot module is
imported, so tests never touch the working copy and never need an API key (offline / mock LLM only)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="partpilot_test_")
os.environ.update({
    "PARTPILOT_RAW_DIR": os.path.join(TMP, "raw"), "PARTPILOT_CLEAN_DIR": os.path.join(TMP, "clean"),
    "PARTPILOT_STATE_DIR": os.path.join(TMP, "state"), "PARTPILOT_REPORT": os.path.join(TMP, "cleaning_report.md"),
    "PARTPILOT_AUDIT": os.path.join(TMP, "state", "audit.jsonl"), "PARTPILOT_USERS": os.path.join(TMP, "users.json"),
    "PARTPILOT_LLM_PROVIDER": "offline",
})
for k in ("PARTPILOT_MODEL", "PARTPILOT_API_KEY", "PARTPILOT_BASE_URL"):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "data"))

import generate  # noqa: E402
import pytest  # noqa: E402

generate.write(generate.build()[0], os.environ["PARTPILOT_RAW_DIR"])
from partpilot import clean  # noqa: E402

CLEAN_RESULT = clean.run()

from partpilot import engine as E  # noqa: E402
from partpilot.perm import User  # noqa: E402


@pytest.fixture(scope="session")
def D():
    return E.data()


@pytest.fixture(scope="session")
def pn(D):
    """Internal part number from a manufacturer part number (or description for parts without an MPN)."""
    def f(key):
        for p, r in D.parts.items():
            if r["MfrPartNo"] == key or r["Description"] == key:
                return p
        raise KeyError(key)
    return f


@pytest.fixture
def users():
    return {"planner": User("planner", "planner", "ALL"), "po_boards": User("po_boards", "product_owner", "BRD"),
            "po_printers": User("po_printers", "product_owner", "PRN"), "lead_nrg": User("lead_nrg", "plant_lead", "NRG"),
            "eng_passives": User("eng_passives", "component_engineer", "Passives"),
            "eng_semis": User("eng_semis", "component_engineer", "Semiconductors"),
            "eng_mech": User("eng_mech", "component_engineer", "Mechanical"), "auditor": User("auditor", "auditor", "SYSTEM")}


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolated approvals/audit state per test."""
    monkeypatch.setattr(E, "STATE", str(tmp_path))
    monkeypatch.setenv("PARTPILOT_AUDIT", str(tmp_path / "audit.jsonl"))
    E.reload()
    yield tmp_path
    E.reload()
