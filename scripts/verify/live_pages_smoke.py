"""GET every endpoint the UI calls while a page is opening.

A 4xx or 5xx fails the run. An ontology body whose source is ``pack``, or whose
``space_id`` is not the one on the query, fails the run. Response bodies are
not printed.

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
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

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
        (f"/v1/ontology?space_id={finance}", "fetchOntology(activeSpaceId"),
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


def ontology_lie(path: str, body: object) -> str | None:
    """A named problem when an ontology GET is not that Space's store. No rows."""
    route = path.split("?", 1)[0]
    if not route.startswith("/v1/ontology"):
        return None
    if not isinstance(body, dict):
        return "not-json"
    wanted = parse_qs(urlsplit(path).query).get("space_id", [""])[0]
    if not wanted:
        return "missing-space-id"
    if body.get("space_id") != wanted:
        return "space-mismatch"
    source = body.get("source")
    if source == "pack":
        return "source-pack"
    if body.get("ok") is True and source != "store":
        return "ok-without-store"
    if body.get("ok") is False and body.get("reason") not in {
        "empty_store",
        "store_unreadable",
        "space_required",
    }:
        return "unnamed-reason"
    return _ontology_lists_problem(body)


_ONTOLOGY_LISTS = ("object_types", "link_types", "action_types", "functions", "metrics")


def _ontology_lists_problem(body: dict[str, Any]) -> str | None:
    """The one GET carries every list the page renders, and the count matches it.

    A missing list, or a count that is not the list length, is a dropped read.
    """
    counts = body.get("counts")
    if not isinstance(counts, dict):
        return "counts-missing"
    for key in _ONTOLOGY_LISTS:
        rows = body.get(key)
        if not isinstance(rows, list):
            return f"{key}-missing"
        if counts.get(key) != len(rows):
            return f"{key}-count-mismatch"
    if not isinstance(body.get("nodes"), list) or not isinstance(body.get("edges"), list):
        return "graph-missing"
    return None


def ontology_page_problems() -> list[str]:
    """The page reads object_types off the one body and renders each object."""
    root = ROOT / "apps/ui/src"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            root / "lib/api.ts",
            root / "lib/ontologyRead.ts",
            root / "pages/OntologyPage.tsx",
        )
    )
    problems: list[str] = []
    for needle in (
        "fetchOntology(activeSpaceId",
        "ontologyBundle(",
        "objects: body.object_types",
        "objects.map((o)",
    ):
        if needle not in text:
            problems.append(needle)
    for section in ("objects", "links", "actions", "functions", "metrics", "graph"):
        call = f'ontologyReadPath("{section}"'
        if call in text:
            problems.append(call)
    return problems


def _sources_space_id(path: str) -> str | None:
    parts = path.split("?", 1)[0].strip("/").split("/")
    if len(parts) == 4 and parts[0] == "v1" and parts[1] == "spaces" and parts[3] == "sources":
        return parts[2]
    return None


def sources_problem(path: str, body: object) -> str | None:
    """A named problem when a Space sources GET is not that Space's rows.

    No row values. ``empty`` is a failure: the memory fixture used to return
    an empty list after a Query default leaked in, and ``count == len``
    still passed.
    """
    space_id = _sources_space_id(path)
    if space_id is None:
        return None
    if not isinstance(body, dict):
        return "not-json"
    if body.get("space_id") != space_id:
        return "space-mismatch"
    sources = body.get("sources")
    if not isinstance(sources, list):
        return "sources-not-list"
    if body.get("count") != len(sources):
        return "count-mismatch"
    if len(sources) < 1:
        return "empty"
    for row in sources:
        if not isinstance(row, dict) or row.get("space_id") != space_id:
            return "foreign-source"
    return None


def sources_overlap(left: object, right: object) -> str | None:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return "not-json"
    left_rows = left.get("sources")
    right_rows = right.get("sources")
    if not isinstance(left_rows, list) or not isinstance(right_rows, list):
        return "sources-not-list"
    left_ids = {row.get("id") for row in left_rows if isinstance(row, dict)}
    right_ids = {row.get("id") for row in right_rows if isinstance(row, dict)}
    if left_ids & right_ids:
        return "overlap"
    return None


def _checker_problems() -> list[str]:
    space = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    other = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    path = f"/v1/spaces/{space}/sources"
    good = {"space_id": space, "count": 1, "sources": [{"id": "a", "space_id": space}]}
    empty = {"space_id": space, "count": 0, "sources": []}
    foreign = {"space_id": space, "count": 1, "sources": [{"id": "a", "space_id": other}]}
    disjoint = {"space_id": other, "count": 1, "sources": [{"id": "b", "space_id": other}]}
    problems: list[str] = []
    if sources_problem(path, good) is not None:
        problems.append("good")
    if sources_problem(path, empty) != "empty":
        problems.append("empty")
    if sources_problem(path, foreign) != "foreign-source":
        problems.append("foreign")
    if sources_overlap(good, disjoint) is not None:
        problems.append("disjoint")
    if sources_overlap(good, good) != "overlap":
        problems.append("overlap")
    return problems


def _empty_ontology_body(space: str) -> dict[str, Any]:
    counts = {key: 0 for key in _ONTOLOGY_LISTS}
    body: dict[str, Any] = {
        "ok": False,
        "source": "none",
        "reason": "empty_store",
        "space_id": space,
        "counts": counts,
        "nodes": [],
        "edges": [],
    }
    for key in _ONTOLOGY_LISTS:
        body[key] = []
    return body


def _ontology_checker_problems() -> list[str]:
    space = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    path = f"/v1/ontology?space_id={space}"
    empty = _empty_ontology_body(space)
    problems: list[str] = []
    if ontology_lie(path, empty) is not None:
        problems.append("empty-ontology")
    hidden = _empty_ontology_body(space)
    hidden["object_types"] = [{"id": "x"}]
    if ontology_lie(path, hidden) != "object_types-count-mismatch":
        problems.append("object-count")
    dropped = _empty_ontology_body(space)
    del dropped["object_types"]
    if ontology_lie(path, dropped) != "object_types-missing":
        problems.append("objects-missing")
    return problems


def source_scope_failures(fetch: Callable[[str], tuple[int, object]]) -> list[str]:
    """Finance and Ops stay apart, and a missing Space is 404. No row text."""
    from dms_core.control_plane.seed import SPACE_MARGIN, SPACE_Q3

    finance = f"/v1/spaces/{SPACE_Q3}/sources"
    ops = f"/v1/spaces/{SPACE_MARGIN}/sources"
    bad: list[str] = []
    bodies: dict[str, object] = {}
    for path in (finance, ops):
        try:
            status, body = fetch(path)
        except Exception as exc:  # noqa: BLE001 - path and type, never a body
            bad.append(f"{path} {type(exc).__name__}")
            continue
        if status != 200:
            bad.append(f"{path} {status}")
            continue
        problem = sources_problem(path, body)
        if problem:
            bad.append(f"{path} {problem}")
            continue
        bodies[path] = body
    if finance in bodies and ops in bodies:
        overlap = sources_overlap(bodies[finance], bodies[ops])
        if overlap:
            bad.append(f"sources {overlap}")
    missing = f"/v1/spaces/{uuid4()}/sources"
    try:
        status, body = fetch(missing)
    except Exception as exc:  # noqa: BLE001
        bad.append(f"missing {type(exc).__name__}")
        return bad
    if status != 404:
        bad.append(f"missing {status}")
    elif not isinstance(body, dict) or body.get("detail") != "space_not_found":
        bad.append("missing detail")
    return bad


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
    checker = _checker_problems() + _ontology_checker_problems()
    render = ontology_page_problems()
    if missing or checker or render:
        print("live_pages_smoke self-check failed")
        for needle in missing:
            print(needle)
        for problem in checker:
            print(f"checker {problem}")
        for problem in render:
            print(f"ontology {problem}")
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
        if res.status_code < 400 and path.split("?", 1)[0].startswith("/v1/ontology"):
            try:
                payload: Any = res.json()
            except ValueError:
                payload = None
            lie = ontology_lie(path, payload)
            if lie:
                raise RuntimeError(lie)
        return res.status_code

    def fetch(path: str) -> tuple[int, object]:
        res = httpx.get(f"{origin}{path}", timeout=30.0)
        try:
            payload: object = res.json()
        except ValueError:
            payload = None
        return res.status_code, payload

    bad = failing_paths(status_of)
    bad.extend(source_scope_failures(fetch))
    if bad:
        print("live_pages_smoke failed")
        for line in bad:
            print(line)
        return 1
    print(f"live_pages_smoke ok {len(page_load_paths())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
