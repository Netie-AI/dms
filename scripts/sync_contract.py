"""Vendor Cortex contract artifacts and (optionally) regenerate the HTTP client.

Copies from CORTEX_ROOT (default D:\\Cortex or $CORTEX_ROOT):
  - contract/openapi-1.1.0.json + .sha256
  - contract/testvectors/manifest_canonical.jsonl

Never hand-edit vendored files. CI fails if hashes drift from the pinned release.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC_NAME = "openapi-1.1.0.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def sync(cortex_root: Path) -> None:
    src_spec = cortex_root / "contract" / SPEC_NAME
    src_hash = cortex_root / "contract" / f"{SPEC_NAME}.sha256"
    src_vectors = cortex_root / "contract" / "testvectors" / "manifest_canonical.jsonl"
    for p in (src_spec, src_hash, src_vectors):
        if not p.is_file():
            raise SystemExit(f"missing Cortex artifact: {p}")

    dest_contract = ROOT / "contract"
    dest_contract.mkdir(parents=True, exist_ok=True)
    (dest_contract / "testvectors").mkdir(parents=True, exist_ok=True)

    shutil.copy2(src_spec, dest_contract / SPEC_NAME)
    shutil.copy2(src_hash, dest_contract / f"{SPEC_NAME}.sha256")
    shutil.copy2(src_vectors, dest_contract / "testvectors" / "manifest_canonical.jsonl")

    expected = (dest_contract / f"{SPEC_NAME}.sha256").read_text(encoding="utf-8").split()[0]
    actual = _sha256(dest_contract / SPEC_NAME)
    if actual != expected:
        raise SystemExit(f"sha256 mismatch for {SPEC_NAME}: {actual} != {expected}")

    vec = dest_contract / "testvectors" / "manifest_canonical.jsonl"
    vec_hash = _sha256(vec)
    (dest_contract / "testvectors" / "manifest_canonical.jsonl.sha256").write_text(
        f"{vec_hash}  manifest_canonical.jsonl\n", encoding="utf-8"
    )
    print(f"synced {SPEC_NAME} ({actual[:12]}…) + testvectors ({vec_hash[:12]}…)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cortex-root",
        type=Path,
        default=Path(__import__("os").environ.get("CORTEX_ROOT", r"D:\Cortex")),
    )
    args = p.parse_args()
    sync(args.cortex_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
