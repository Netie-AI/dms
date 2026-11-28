"""Print a pbkdf2 hash for DMS_SSO_BOOTSTRAP_FILE. Password is read from stdin.

The hash is the only thing written. The password is not echoed and not stored.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap() -> None:
    root = Path(__file__).resolve().parents[2]
    entry = str(root / "apps" / "api")
    if entry not in sys.path:
        sys.path.insert(0, entry)


def main() -> int:
    _bootstrap()
    from dms_api.auth_sso import AuthError, hash_password

    password = sys.stdin.read().strip("\r\n")
    try:
        sys.stdout.write(hash_password(password) + "\n")
    except AuthError as exc:
        sys.stderr.write(exc.code + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
