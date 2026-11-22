"""Username/password login. Passwords are stored only as salted PBKDF2-SHA256 hashes in config/users.json
(no plain-text passwords in code or in the repository).

  python -m partpilot.auth init config/accounts.example.csv   create accounts from a CSV (username,role,scope);
                                                               password from PARTPILOT_DEMO_PASSWORD or a prompt
  python -m partpilot.auth add <user> <role> <scope>           add or reset one account (password prompt)
"""
from __future__ import annotations

import csv
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys

from .perm import User

ITERATIONS = 200_000


def users_path():
    return os.getenv("PARTPILOT_USERS", "config/users.json")


def _hash(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), ITERATIONS).hex()


def load():
    p = users_path()
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def add(username, role, scope, password):
    if not password or len(password) < 8:
        raise ValueError("password must have at least 8 characters")
    User(username, role, scope)  # validates role and scope
    db = load()
    salt = secrets.token_hex(16)
    db[username] = {"role": role, "scope": scope, "salt": salt, "hash": _hash(password, salt)}
    p = users_path()
    if os.path.dirname(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)


def login(username, password) -> User | None:
    u = load().get((username or "").strip())
    if not u:
        _hash(password or "", "00" * 16)  # same work for unknown users (no timing hint)
        return None
    if not hmac.compare_digest(_hash(password or "", u["salt"]), u["hash"]):
        return None
    return User(username.strip(), u["role"], u["scope"])


def init_from_csv(path, password):
    with open(path, encoding="utf-8-sig") as f:
        accounts = [(r["username"].strip(), r["role"].strip(), r["scope"].strip()) for r in csv.DictReader(f) if r.get("username")]
    for u, r, sc in accounts:
        add(u, r, sc, password)
    return len(accounts)


def password_from_env_or_prompt(prompt="password: "):
    """Passwords are never in code: PARTPILOT_DEMO_PASSWORD (set by whoever runs the demo) or an interactive prompt."""
    return os.environ.get("PARTPILOT_DEMO_PASSWORD") or getpass.getpass(prompt)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "init":
        n = init_from_csv(argv[1], password_from_env_or_prompt("password for these accounts: "))
        print(f"saved {n} accounts to {users_path()}")
    elif len(argv) >= 4 and argv[0] == "add":
        add(argv[1], argv[2], argv[3], getpass.getpass(f"password for {argv[1]}: "))
        print("saved")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
