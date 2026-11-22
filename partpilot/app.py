"""Command-line app: login -> role-aware Q&A.   Run:  python -m partpilot.app
Commands: /new (new conversation)  /quit
  auditor only            : /clean (run the cleaning job; prints counts only)   /audit (last audit records, metadata only)
  component engineer only : /review (pending items of your commodities)   /approve <ID>   /reject <ID>"""
from __future__ import annotations

import getpass
import sys

from . import auth, review
from .perm import Denied


def handle_command(user, q, conv_factory):
    """Returns (output_text, new_conversation_or_None)."""
    if q == "/new":
        return "(new conversation)", conv_factory()
    if q == "/clean":
        if user.role != "auditor":
            return "Only the Auditor can start the cleaning job.", None
        from . import clean, engine
        r = clean.run()
        engine.reload()
        return f"cleaning done; usable rows: { {k: v[1] for k, v in r['counts'].items()} }; pending items: {r['pending']}", None
    if q == "/audit":
        if user.role != "auditor":
            return "Only the Auditor can read audit logs.", None
        return "\n".join(str(r) for r in review.audit_metadata(20)) or "(audit log empty)", None
    if q.startswith(("/review", "/approve", "/reject")):
        try:
            if q == "/review":
                items = review.items_for(user)
                rows = [f"{p['id']} {p['kind']} {p['key']} decision:{p['decision']}" for p in items[:40]]
                return "\n".join(rows + [f"{len(items)} items for {user.scope}"]), None
            parts = q.split()
            if len(parts) != 2:
                return "usage: /approve <ID> or /reject <ID>", None
            r = review.decide(user, parts[1], "approve" if parts[0] == "/approve" else "reject")
            return f"recorded {r}", None
        except Denied as e:
            return str(e), None
    return None, None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    user = auth.login(input("username: ").strip(), getpass.getpass("password: "))
    if not user:
        print("Login failed.")
        return
    from . import llm
    from .agent import Conversation
    print(f"Logged in as {user.username} ({user.role_name}, scope {user.scope}); LLM provider: {llm.provider()}. "
          "/new for a new conversation, /quit to exit.")
    conv = Conversation(user)
    while True:
        q = input("\n> ").strip()
        if not q:
            continue
        if q == "/quit":
            break
        out, new = handle_command(user, q, lambda: Conversation(user))
        if new is not None:
            conv = new
        if out is not None:
            print(out)
            continue
        print(conv.ask(q))
        s = conv.last_stats
        print(f"\n[tokens in {s['in_tokens']} / out {s['out_tokens']}, tools {s['tool_calls']}, {s['seconds']} s, est. ${s['cost_usd']}]")


if __name__ == "__main__":
    main()
