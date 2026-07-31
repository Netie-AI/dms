"""Prune vendored OpenAPI to contract paths only for openapi-python-client."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF_RE = re.compile(r"#/components/schemas/(.+)$")


def _walk_refs(obj: object, refs: set[str]) -> None:
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str):
            m = REF_RE.match(ref)
            if m:
                refs.add(m.group(1))
        for v in obj.values():
            _walk_refs(v, refs)
    elif isinstance(obj, list):
        for v in obj:
            _walk_refs(v, refs)


def prune(src: dict) -> dict:
    paths = src["paths"]
    schemas = src.get("components", {}).get("schemas", {})
    refs: set[str] = set()
    _walk_refs(paths, refs)
    changed = True
    while changed:
        changed = False
        for name in list(refs):
            if name not in schemas:
                continue
            before = len(refs)
            _walk_refs(schemas[name], refs)
            if len(refs) != before:
                changed = True

    # Resolve title collisions: keep Contract* when both exist; rewrite $refs.
    rename: dict[str, str] = {}
    for name in list(refs):
        if name.startswith("Contract"):
            continue
        pref = f"Contract{name}"
        if pref in refs or pref in schemas:
            rename[name] = pref
            refs.discard(name)
            refs.add(pref)

    def rewrite(obj: object) -> object:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "$ref" and isinstance(v, str):
                    m = REF_RE.match(v)
                    if m and m.group(1) in rename:
                        out[k] = f"#/components/schemas/{rename[m.group(1)]}"
                    else:
                        out[k] = v
                else:
                    out[k] = rewrite(v)
            return out
        if isinstance(obj, list):
            return [rewrite(v) for v in obj]
        return obj

    paths2 = rewrite(paths)
    keep = {n: rewrite(schemas[n]) for n in refs if n in schemas}
    missing = sorted(n for n in refs if n not in schemas)
    if missing:
        raise SystemExit(f"missing schemas: {missing}")

    return {
        "openapi": src.get("openapi", "3.1.0"),
        "info": {
            "title": "Cortex Contract API",
            "version": src.get("info", {}).get("version", "1.2.0"),
            "description": "Pruned generation surface — five contract operationIds only.",
        },
        "paths": paths2,
        "components": {"schemas": keep},
    }


def main() -> int:
    src_path = ROOT / "contract" / "openapi-1.2.0.json"
    dest = ROOT / "contract" / "openapi-1.2.0.gen.json"
    src = json.loads(src_path.read_text(encoding="utf-8"))
    out = prune(src)
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {dest} paths={len(out['paths'])} schemas={len(out['components']['schemas'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
