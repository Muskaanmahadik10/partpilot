"""First-run setup used by `docker compose up` and the web UI:
generate the seeded raw data if missing, clean it if the clean store is missing, and create the demo accounts.

  python -m partpilot.bootstrap

Demo accounts get the password from PARTPILOT_DEMO_PASSWORD. If it is not set, a random password is generated and
printed ONCE to the console (it is never written to a file in plain text)."""
from __future__ import annotations

import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_data(force: bool = False) -> dict:
    from . import clean, engine
    raw = clean.RAW
    done = {}
    if force or not os.path.exists(os.path.join(raw, "parts_master.csv")):
        sys.path.insert(0, os.path.join(ROOT, "data"))
        import generate
        done["generated"] = generate.write(generate.build()[0], raw)
    if force or done or not os.path.exists(os.path.join(clean.OUT, "clean.pkl")):
        done["cleaned"] = clean.run()["counts"]
        engine.reload()
    return done


def ensure_accounts(csv_path: str | None = None) -> str | None:
    """Returns the generated password when one was created, else None."""
    from . import auth
    if auth.load():
        return None
    pw = os.environ.get("PARTPILOT_DEMO_PASSWORD")
    generated = None
    if not pw:
        pw = generated = secrets.token_urlsafe(12)
    auth.init_from_csv(csv_path or os.path.join(ROOT, "config", "accounts.example.csv"), pw)
    return generated


def main():
    done = ensure_data()
    print("data:", done or "already prepared")
    gen = ensure_accounts()
    if gen:
        print("demo accounts created; password for every demo account (shown once):", gen)
    else:
        print("demo accounts: ready")


if __name__ == "__main__":
    main()
