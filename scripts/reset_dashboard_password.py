"""Local-only dashboard password recovery.

This command never prints or stores the password and does not expose an API
route. Run it from the ATLAX project root.
"""

from __future__ import annotations

from getpass import getpass
import sys
from pathlib import Path

# Running a script by path places ``scripts`` first on sys.path. Add the
# repository root so the recovery utility imports the ATLAX package reliably.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.api.auth_db import AuthDB


def main() -> int:
    username = input("Dashboard username [quraish]: ").strip() or "quraish"
    password = getpass("New dashboard password: ")
    confirmation = getpass("Confirm new dashboard password: ")
    if len(password) < 12:
        print("Password must contain at least 12 characters.")
        return 2
    if password != confirmation:
        print("Passwords do not match.")
        return 2

    updated = AuthDB().reset_password(username, password)
    if not updated:
        print(f"No dashboard user named {username!r} was found.")
        return 1
    print(f"Dashboard password updated for {username!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
