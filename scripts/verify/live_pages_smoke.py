"""GET every endpoint the UI calls while a page is opening.

A 4xx or 5xx fails the run. Response bodies are not printed.

The paths come from apps/ui/src/lib/api.ts and the page modules. Flag-off
routes that are not mounted (insight-proof, studio adjust) are omitted: the
UI treats that 404 as the feature being off, and the flags default off.

``--self-check`` confirms each needle is still in the UI source. It does not
boot an API. ``--base URL`` talks to a booted API (CI, seeded Postgres).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def page_load_paths() -> list[tuple[str, str]]:
    """(request path, substring that must stay in the UI source)."""
    from dms_core.control_plane.seed import SPACE_MARGIN, SPACE_Q3

    finance = str(SPACE_Q3)
    ops = str(SPACE_MARGIN)
    return [
        ("/health", "/health"),
        ("/v1/spaces", "/v1/spaces"),
        ("/v1/library/sources", "/v1/library/sources"),
        ("/v1/library/tree", "/v1/library/tree"),
        ("/v1/ontology", "/v1/ontology"),
        ("/v1/ontology/objects", "/v1/ontology/objects"),
        ("/v1/ontology/links", "/v1/ontology/links"),
        ("/v1/ontology/actions", "/v1/ontology/actions"),
        ("/v1/ontology/functions", "/v1/ontology/functions"),
        ("/v1/ontology/metrics", "/v1/ontology/metrics"),
        ("/v1/ontology/graph", "/v1/ontology/graph"),
        ("/v1/trust/summary", "/v1/trust/summary"),
        ("/v1/runs", "/v1/runs"),
        ("/v1/amend/proposals", "/v1/amend/proposals"),
        ("/v1/admin/overview", "/v1/admin/overview"),
        ("/v1/audit/ledger", "/v1/audit/ledger"),
        (
            f"/v1/spaces/{finance}/sources",
            "/v1/spaces/${encodeURIComponent(spaceId)}/sources",
        ),
        (
            f"/v1/spaces/{ops}/sources",
            "/v1/spaces/${encodeURIComponent(spaceId)}/sources",
        ),
        (f"/v1/studio/verified-queries?space_id={finance}", "/v1/studio/verified-queries"),
        (f"/v1/library/sources?space_id={finance}", "/v1/library/sources"),
        (f"/v1/library/tree?space_id={finance}", "/v1/library/tree"),
        (f"/v1/runs?space_id={finance}", "/v1/runs"),
        (f"/v1/amend/proposals?space_id={finance}", "/v1/amend/proposals"),
    ]


def failing_paths(status_of: Callable[[str], int]) -> list[str]:
    """Paths whose status is 4xx or 5xx, or whose call raised. No bodies."""
    bad: list[str] = []
    for path, _needle in page_load_paths():
        try:
            status = int(status_of(path))
        except Exception as exc:  # noqa: BLE001 - the path and the type, never a body
            bad.append(f"{path} {type(exc).__name__}")
            continue
        if status >= 400:
            bad.append(f"{path} {status}")
    return bad


def _ui_text() -> str:
    parts = [(ROOT / "apps/ui/src/lib/api.ts").read_text(encoding="utf-8")]
    pages = ROOT / "apps/ui/src/pages"
    parts.extend(path.read_text(encoding="utf-8") for path in sorted(pages.glob("*.tsx")))
    return "\n".join(parts)


def self_check() -> int:
    text = _ui_text()
    missing = sorted({needle for _path, needle in page_load_paths() if needle not in text})
    if missing:
        print("live_pages_smoke self-check: UI no longer contains:")
        for needle in missing:
            print(needle)
        return 1
    if not page_load_paths():
        print("live_pages_smoke self-check: empty path list")
        return 1
    print(f"live_pages_smoke self-check ok {len(page_load_paths())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Page-load GET smoke")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--base", default="", help="booted API origin, no trailing path")
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.base:
        print("pass --base URL or --self-check")
        return 2
    import httpx

    origin = args.base.rstrip("/")

    def status_of(path: str) -> int:
        res = httpx.get(f"{origin}{path}", timeout=30.0)
        return res.status_code

    bad = failing_paths(status_of)
    if bad:
        print("live_pages_smoke failed")
        for line in bad:
            print(line)
        return 1
    print(f"live_pages_smoke ok {len(page_load_paths())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
