"""Build the model-comparison table from reports/eval_*.json (metrics recomputed from the per-case rows).

  python eval/summary.py   -> prints Markdown and writes reports/eval_summary.md"""
from __future__ import annotations

import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "eval"))

from run_eval import summarize  # noqa: E402


def table(paths):
    rows = ["| Setup | Accuracy (20 cases) | Permission/injection violations (10 probes) | Forbidden claims (all cases) | "
            "Avg tool calls | Avg tokens in / out | Avg latency | Failed cases |", "|---|---|---|---|---|---|---|---|"]
    for p in paths:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        s0 = d["summary"]
        s = summarize(d["rows"], s0["provider"], s0["model"])
        name = "offline rules router (no LLM)" if s["provider"] == "offline" else f"{s['provider']} {s['model']}"
        if "before_mpn_fix" in p:
            name += " - before the MPN fix"
        fails = ", ".join(r["id"] for r in d["rows"] if not r["pass"]) or "none"
        calls = sum(len(r["tools"]) for r in d["rows"]) / len(d["rows"])
        rows.append(f"| {name} | {s['accuracy']:.0%} | {s['permission_violation_rate']:.0%} | {s['forbidden_claim_rate']:.0%} | "
                    f"{calls:.1f} | {s['avg_in_tokens']} / {s['avg_out_tokens']} | {s['avg_seconds']} s | {fails} |")
    return "\n".join(rows)


def main():
    paths = sorted(glob.glob(os.path.join(ROOT, "reports", "eval_*.json")))
    md = table(paths)
    print(md)
    with open(os.path.join(ROOT, "reports", "eval_summary.md"), "w", encoding="utf-8") as f:
        f.write(md + "\n")


if __name__ == "__main__":
    main()
