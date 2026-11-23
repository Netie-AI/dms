"""ACT-02 proof: with ``DMS_ACTIONS`` unset, this tree serves what the base tree serves.

Usage, from a worktree ``$W`` of this branch::

    python scripts/verify/act_flag_off_parity.py --base origin/main [--workdir DIR]

It adds a detached git worktree of ``--base`` under ``--workdir`` (a fresh temp
directory by default), then in one child process per tree, with ``DMS_ACTIONS``
and ``DATABASE_URL`` unset and ``PYTHONPATH`` pointing only at that tree, it
captures:

* the sha256 of ``/openapi.json`` (``json.dumps(..., sort_keys=True)``);
* the status, content type and body of every ACT-02 route with its own method
  and a malformed or mis-shaped body, and of one other method per route;
* the 404 body of ``POST /v1/amend/no-such-route``.

It prints the sha256 pairs, exits non-zero on any difference, and removes the
base worktree (``git worktree remove``). Nothing here touches Postgres or a
warehouse file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG_DIRS = (
    "apps/api",
    "packages/core",
    "packages/cortex_client",
    "packages/executor",
    "packages/ledger",
    "packages/ao_vendor",
)

PROBE = r"""
import hashlib, json, sys, uuid
from fastapi.testclient import TestClient
from dms_api.app import create_app
import dms_api
U = "11111111-2222-3333-4444-555555555555"
S = "66666666-7777-8888-9999-000000000000"
BAD = b'{"space_id": "x", "action_type": '
ROUTES = [
    ("GET", "/v1/amend/actions/types", None),
    ("GET", f"/v1/amend/actions/proposals?space_id={S}", None),
    ("POST", "/v1/amend/actions/proposals", BAD),
    ("POST", f"/v1/amend/actions/proposals/{U}/apply", b'{"idempotency_token": 7}'),
    ("POST", f"/v1/amend/actions/proposals/{U}/decline", b"{}"),
    ("GET", f"/v1/amend/actions/objects?object_type=inventory_item&space_id={S}", None),
    ("GET", f"/v1/amend/actions/decisions?space_id={S}", None),
    ("GET", f"/v1/amend/actions/decisions/{U}?space_id={S}", None),
    ("POST", f"/v1/amend/actions/decisions/{U}/revert", b"[not json"),
    ("POST", "/v1/amend/no-such-route", BAD),
]
app = create_app()
client = TestClient(app)
out = {"dms_api": dms_api.__file__}
out["openapi_sha256"] = hashlib.sha256(
    json.dumps(client.get("/openapi.json").json(), sort_keys=True).encode()
).hexdigest()
probes = {}
for method, path, body in ROUTES:
    for m in (method, "DELETE" if method != "DELETE" else "PUT"):
        headers = {"content-type": "application/json"} if body is not None else {}
        r = client.request(m, path, content=body if m == method else None, headers=headers)
        probes[f"{m} {path}"] = [r.status_code, r.headers.get("content-type"), r.text]
out["probes"] = probes
print(json.dumps(out, sort_keys=True))
"""


def _probe(tree: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in {"DMS_ACTIONS", "DATABASE_URL"}}
    env["PYTHONPATH"] = os.pathsep.join(str(tree / d) for d in PKG_DIRS)
    env["PYTHONUTF8"] = "1"
    env.setdefault("DMS_DEMO_FALLBACK", "0")
    env.setdefault("OPENVAULT_URL", "http://127.0.0.1:9")
    done = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=str(tree),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"probe failed in {tree}:\n{done.stderr[-2000:]}")
    line = done.stdout.strip().splitlines()[-1]
    data = json.loads(line)
    where = Path(data["dms_api"]).resolve()
    if tree.resolve() not in where.parents:
        raise SystemExit(f"probe in {tree} imported dms_api from {where}")
    return data


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    git = ["git", "-C", str(ROOT)]
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    base_sha = subprocess.run(
        [*git, "rev-parse", args.base], capture_output=True, text=True, check=True
    ).stdout.strip()
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="act-parity-"))
    base_tree = workdir / "act-base"
    subprocess.run(
        [*git, "worktree", "add", "--detach", str(base_tree), base_sha],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        print(f"head  {head}  {ROOT}")
        print(f"base  {base_sha}  {args.base}  {base_tree}")
        ours = _probe(ROOT)
        theirs = _probe(base_tree)
    finally:
        subprocess.run(
            [*git, "worktree", "remove", "--force", str(base_tree)],
            check=False,
            capture_output=True,
            text=True,
        )

    diffs: list[str] = []
    print(f"openapi.json sha256  head {ours['openapi_sha256']}")
    print(f"                     base {theirs['openapi_sha256']}")
    if ours["openapi_sha256"] != theirs["openapi_sha256"]:
        diffs.append("openapi.json")
    for key in sorted(theirs["probes"]):
        a, b = ours["probes"].get(key), theirs["probes"][key]
        mark = "same" if a == b else "DIFF"
        print(f"  [{mark}] {key}: {b[0]} {b[2][:40]!r}")
        if a != b:
            diffs.append(f"{key}: head={a} base={b}")
    print(f"probe bodies sha256  head {_sha(ours['probes'])}")
    print(f"                     base {_sha(theirs['probes'])}")
    if diffs:
        print("FLAG-OFF PARITY FAILED:\n" + "\n".join(diffs))
        return 1
    print(f"FLAG-OFF PARITY OK @{head} vs {args.base} {base_sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
