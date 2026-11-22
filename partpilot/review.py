"""Human review of pending items (shared by the CLI and the web UI).

A component engineer sees only the items of their own commodity group and records approve / reject. Approved
pending substitutes become usable in the engine immediately; conflicts stay excluded until the source data is fixed
(the decision is kept as a record for the data owner). Remarks are shown to the human reviewer, never to the LLM."""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import engine as E
from .perm import Denied, User


def _path():
    return os.path.join(E.STATE, "approvals.json")


def items_for(user: User) -> list[dict]:
    if user.role != "component_engineer":
        raise Denied("Only component engineers can review pending items.")
    D = E.data()
    dec = E.Data.decisions()
    remarks = {a["SubID"]: a.get("Remark_HUMAN_ONLY", "") for a in D.alts}
    out = []
    for p in D.pending:
        if not user.can_approve(p["commodity"]):
            continue
        out.append({**p, "decision": dec.get(p["key"], {}).get("decision", "open"),
                    "decided_by": dec.get(p["key"], {}).get("by", ""),
                    "remark_for_humans": remarks.get(p["key"], "") if p["kind"] == "ALTERNATE_PENDING" else ""})
    return out


def decide(user: User, item_id: str, decision: str) -> dict:
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    mine = {p["id"]: p for p in items_for(user)}
    by_key = {p["key"]: p for p in mine.values()}
    p = mine.get(item_id) or by_key.get(item_id)
    if not p:
        raise Denied("Item not found or not in your commodity group.")
    store = E.Data.decisions()
    store[p["key"]] = {"decision": decision, "by": user.username, "at": datetime.now().isoformat(timespec="seconds"),
                       "kind": p["kind"]}
    os.makedirs(E.STATE, exist_ok=True)
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)
    E.reload()
    return store[p["key"]]


def audit_metadata(n: int = 50) -> list[dict]:
    """Auditor view: metadata only - no question text, tool arguments or answers (they could contain parts data)."""
    from .agent import audit_path
    p = audit_path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    out = []
    for line in lines:
        r = json.loads(line)
        out.append({"ts": r.get("ts"), "user": r.get("user"), "role": r.get("role"), "model": r.get("model"),
                    "tools": ", ".join(t["tool"] + (" (DENIED)" if t.get("denied") else "") for t in r.get("tools", [])),
                    "denied_tool_calls": sum(1 for t in r.get("tools", []) if t.get("denied")),
                    "in_tokens": r.get("in_tokens"), "out_tokens": r.get("out_tokens"), "cost_usd": r.get("cost_usd"),
                    "seconds": r.get("seconds")})
    return out
