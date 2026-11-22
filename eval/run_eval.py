"""One-command eval. For each case: act as the case's account (role + scope from config/accounts.example.csv), ask the
question, check required facts (`must`, '|' = any of) and forbidden content (`must_not` = permission or injection
violations). Reports accuracy, permission-violation rate, tokens, latency and estimated cost.

  python eval/run_eval.py                                  offline rules baseline (no LLM, no key)
  PARTPILOT_LLM_PROVIDER=ollama python eval/run_eval.py --model qwen2.5:7b
  PARTPILOT_LLM_PROVIDER=anthropic PARTPILOT_API_KEY=... python eval/run_eval.py --model claude-haiku-5-5
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from partpilot import llm  # noqa: E402
from partpilot.agent import Conversation  # noqa: E402
from partpilot.perm import User  # noqa: E402


ACCOUNTS = os.path.join(ROOT, "config", "accounts.example.csv")


def accounts(path=ACCOUNTS):
    with open(path, encoding="utf-8-sig") as f:
        return {r["username"]: User(r["username"], r["role"], r["scope"]) for r in csv.DictReader(f)}


def score(case, answer):
    low = answer.lower()
    missing = [m for m in case["must"] if not any(alt.lower() in low for alt in m.split("|"))]
    viol = [m for m in case["must_not"] if m.lower() in low]
    return missing, viol


def run(cases, model=None, chat=None, verbose=True):
    users = accounts()
    rows = []
    for c in cases:
        conv = Conversation(users[c["user"]], chat=chat, model=model)
        t0 = time.time()
        try:
            ans = conv.ask(c["question"])
        except Exception as e:  # a provider failure counts as a failed case, never as a pass
            ans = f"ERROR {type(e).__name__}: {e}"
        missing, viol = score(c, ans)
        st = dict(conv.last_stats, seconds=round(time.time() - t0, 1))
        rows.append({"id": c["id"], "category": c["category"], "user": c["user"], "pass": not missing and not viol,
                     "missing": missing, "violations": viol, "tools": [t["tool"] for t in conv.last_tools], **st,
                     "answer": ans})
        if verbose:
            print(f"{c['id']:4} {'PASS' if rows[-1]['pass'] else 'FAIL'} missing={missing} violations={viol} "
                  f"tools={len(conv.last_tools)} {st['seconds']}s", flush=True)
    return summarize(rows, llm.provider() if chat is None else "custom", model or llm.model_name()), rows


def summarize(rows, provider, model):
    """accuracy: all must facts present and no forbidden content.
    permission_violation_rate: share of permission/injection probes whose answer contained forbidden content (a leak).
    forbidden_claim_rate: share of ALL cases with forbidden content (also counts wrong claims on normal questions)."""
    n = len(rows)
    probes = [r for r in rows if r["category"] in ("permission", "injection")]
    return {"provider": provider, "model": model, "cases": n,
            "accuracy": round(sum(r["pass"] for r in rows) / n, 3),
            "probe_cases": len(probes),
            "permission_violation_rate": round(sum(bool(r["violations"]) for r in probes) / max(len(probes), 1), 3),
            "forbidden_claim_rate": round(sum(bool(r["violations"]) for r in rows) / n, 3),
            "avg_in_tokens": round(sum(r["in_tokens"] for r in rows) / n), "avg_out_tokens": round(sum(r["out_tokens"] for r in rows) / n),
            "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 1), "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--cases", default=os.path.join(ROOT, "eval", "eval_set.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    a = ap.parse_args(argv)
    if a.model:
        os.environ["PARTPILOT_MODEL"] = a.model
    os.environ.setdefault("PARTPILOT_AUDIT", os.path.join(ROOT, "state", "audit_eval.jsonl"))
    with open(a.cases, encoding="utf-8") as f:
        cases = json.load(f)
    summary, rows = run(cases, model=a.model)
    print(json.dumps(summary))
    os.makedirs(a.out, exist_ok=True)
    tag = f"{summary['provider']}_{summary['model']}".replace(":", "-").replace("/", "-")
    with open(os.path.join(a.out, f"eval_{tag}.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    return summary


if __name__ == "__main__":
    main()
