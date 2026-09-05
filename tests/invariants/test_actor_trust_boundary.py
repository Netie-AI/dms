"""DR-0004 Option A - identity is resolved server-side, never from a request.

The Confirmation section of DR-0004 names two assertions that must exist before the
record may move from ``proposed`` to ``accepted``:

  1. no request field and no request header can determine a ledger actor;
  2. under Option B only, a request with no credential is refused.

This file supplies (1). (2) is deliberately absent - Option A was chosen, and a test
asserting a credential check would be asserting a control that does not exist.

R-0001: these assert on what a client actually sees - the HTTP response and the request
schema - not on an internal call. A caller cannot send a field that pydantic does not
declare, and cannot send a header the middleware refuses.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dms_api.middleware_actor import IDENTITY_HEADERS
from fastapi.testclient import TestClient

#: Cheap route that exists and does no network probing. ``/health`` would work too but
#: it live-probes Cortex and OpenVault, which is seconds per call and unrelated to what
#: is under test here.
PROBE_PATH = "/openapi.json"


@pytest.fixture(scope="module")
def client() -> TestClient:
    from dms_api.app import create_app

    return TestClient(create_app())


@pytest.mark.parametrize("header", IDENTITY_HEADERS)
def test_an_identity_header_is_refused_not_ignored(client: TestClient, header: str) -> None:
    """A request that names its own identity is turned away, and the refusal says why.

    Ignoring the header would be the silent form (R-0011): the caller believes it acted
    as someone, the server believes otherwise, and nothing on the wire says which.
    """
    resp = client.get(PROBE_PATH, headers={header: "someone-else"})

    assert resp.status_code == 400, (
        f"{header} was accepted or ignored - identity must come from configuration"
    )
    body = resp.json()
    assert body["code"] == "identity_header_not_accepted"
    assert header in body["headers"]
    assert "DR-0004" == body["decision_record"]


def test_the_same_request_without_the_header_is_fine(client: TestClient) -> None:
    """R-0005 - the control must refuse the header, not the traffic."""
    resp = client.get(PROBE_PATH)
    assert resp.status_code == 200


def test_identity_headers_are_refused_case_insensitively(client: TestClient) -> None:
    """HTTP header names are case-insensitive; a control that is not would be a hole."""
    resp = client.get(PROBE_PATH, headers={"X-DMS-Role": "admin"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "identity_header_not_accepted"


def test_no_request_field_can_name_the_ledger_actor() -> None:
    """A-0005 companion to the header check, asserted on the request schema itself.

    ``GoldSignBody`` used to declare ``steward_id``, which became the ledger actor. The
    field is gone; this pins it gone. Together with the header tests above, both halves
    of "identity never arrives on a request" are covered.
    """
    from dms_api.routes.pipelines import GoldSignBody

    declared = set(GoldSignBody.model_fields)
    forbidden = {"steward_id", "actor", "actor_user_id", "tenant_id", "role", "user_id"}
    leaked = declared & forbidden
    assert not leaked, f"GoldSignBody declares identity fields a caller could set: {leaked}"


def test_the_middleware_that_read_headers_is_gone() -> None:
    """The rejected Option C was to wire the old header middleware through.

    DR-0004 records that as rejected so it is not proposed again. If the class comes
    back, this fails and points at the record rather than at a preference.
    """
    import dms_api.middleware_actor as mod

    assert not hasattr(mod, "DevActorMiddleware"), (
        "DevActorMiddleware is back. DR-0004 Option C was rejected: x-dms-role is "
        "caller-supplied and unverified, so honouring it is privilege escalation."
    )


# --- EPIC-025: no request field reaches ledger actor or attestation -------------
#
# dms#74 found ten ungated GETs where five were reported, by classifying routes
# on what they REACH rather than trusting a list. The same move here: scan every
# request body on a route, every ledger-append actor=, and every GoldMetricDef
# built in apps/api. Do not trust GoldSignBody alone.

ROOT = Path(__file__).resolve().parents[2]
ROUTES_DIR = ROOT / "apps" / "api" / "dms_api" / "routes"
WIRING_PATH = ROOT / "apps" / "api" / "dms_api" / "wiring.py"

FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
        "steward_id",
        "actor",
        "actor_user_id",
        "tenant_id",
        "role",
        "user_id",
        "signature",
        "signed_at",
        "ledger_entry_id",
    }
)
ATTESTATION_FIELD_SET = frozenset({"signature", "signed_at", "ledger_entry_id", "steward_id"})
LEDGER_ACTOR_CALLS = frozenset({"LedgerAppendRequest", "append_event", "ledger_append"})
ROUTE_METHODS = frozenset({"get", "post", "put", "patch", "delete"})


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_route(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for deco in fn.decorator_list:
        node = deco.func if isinstance(deco, ast.Call) else deco
        if isinstance(node, ast.Attribute) and node.attr in ROUTE_METHODS:
            return True
    return False


def _ann_name(ann: ast.expr | None) -> str | None:
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        return ann.attr
    return None


def _class_fields(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _request_body_models() -> list[tuple[str, str, set[str]]]:
    """(relpath, ClassName, field names) for every BaseModel used as a route param."""
    found: list[tuple[str, str, set[str]]] = []
    for path in sorted(ROUTES_DIR.glob("*.py")):
        tree = _parse(path)
        classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        rel = path.relative_to(ROOT).as_posix()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_route(node):
                continue
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                name = _ann_name(arg.annotation)
                if name is None or name not in classes:
                    continue
                cls = classes[name]
                bases = [
                    b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                    for b in cls.bases
                ]
                if "BaseModel" not in bases:
                    continue
                found.append((f"{rel}:{cls.lineno}", cls.name, _class_fields(cls)))
    return found


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_request_sourced(expr: ast.AST) -> bool:
    """True when the expression is body.*, request.*, or gold_metric[...]/ .get."""
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name) and sub.id in {"body", "request", "gold_metric"}:
            return True
        if isinstance(sub, ast.Attribute) and sub.attr in {
            "headers",
            "query_params",
            "path_params",
        }:
            return True
    return False


def _ledger_actor_kwargs() -> list[tuple[str, int, ast.AST]]:
    """(relpath, lineno, actor-expr) for actor= on ledger append calls under apps/api."""
    api_root = ROOT / "apps" / "api"
    hits: list[tuple[str, int, ast.AST]] = []
    for path in api_root.rglob("*.py"):
        tree = _parse(path)
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) not in LEDGER_ACTOR_CALLS:
                continue
            for kw in node.keywords:
                if kw.arg != "actor" or kw.value is None:
                    continue
                hits.append((rel, node.lineno, kw.value))
    return hits


def _gold_metric_ctors() -> list[tuple[str, int, ast.Call]]:
    api_root = ROOT / "apps" / "api"
    hits: list[tuple[str, int, ast.Call]] = []
    for path in api_root.rglob("*.py"):
        tree = _parse(path)
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "GoldMetricDef":
                hits.append((rel, node.lineno, node))
    return hits


def test_no_request_body_declares_actor_or_attestation_fields() -> None:
    """Re-derived surface: every route body, not just GoldSignBody.

    A caller-supplied steward_id became the ledger actor (A-0005). A caller-supplied
    signature became is_signed (F70). Both are request fields that must not exist.
    """
    models = _request_body_models()
    offenders: list[str] = []
    for loc, name, fields in models:
        leaked = fields & FORBIDDEN_REQUEST_FIELDS
        if leaked:
            offenders.append(f"{loc} {name}: {sorted(leaked)}")
    assert not offenders, (
        "a request body declares a field that can name a ledger actor or an "
        "attestation:\n" + "\n".join(offenders)
    )


def test_the_request_body_scan_is_not_vacuous() -> None:
    """R-0007 — a scan that finds no models cannot certify the surface is clean."""
    models = _request_body_models()
    names = {name for _loc, name, _fields in models}
    assert len(models) >= 8, (
        "the request-body scan found almost no models, so a green result means "
        f"nothing. Found {len(models)}: {sorted(names)}"
    )
    for required in ("GoldSignBody", "RunBody"):
        assert required in names, f"the scan missed {required}: {sorted(names)}"


def test_attestation_fields_tuple_is_the_full_set() -> None:
    """Emptying ATTESTATION_FIELDS would make pipeline_run's loop a no-op (R-0007)."""
    from dms_api.wiring import ATTESTATION_FIELDS

    assert frozenset(ATTESTATION_FIELDS) == ATTESTATION_FIELD_SET, (
        f"ATTESTATION_FIELDS drifted: {ATTESTATION_FIELDS!r}"
    )


def test_pipeline_run_still_iterates_attestation_fields() -> None:
    """The RunBody.gold_metric dict can carry keys the schema does not declare.

    The named refuse lives in wiring.pipeline_run. If that loop disappears, the
    schema scan above stays green while /run accepts a forged signature again.
    """
    tree = _parse(WIRING_PATH)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "pipeline_run"
    )
    uses_tuple = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == "ATTESTATION_FIELDS":
            uses_tuple = True
            break
    assert uses_tuple, (
        "pipeline_run no longer consults ATTESTATION_FIELDS, so a gold_metric "
        "dict can assert certification again"
    )


def test_ledger_actor_kwargs_are_not_request_sourced() -> None:
    hits = _ledger_actor_kwargs()
    offenders = [
        f"{rel}:{lineno} actor={ast.dump(expr)}"
        for rel, lineno, expr in hits
        if _is_request_sourced(expr)
    ]
    assert not offenders, (
        "ledger actor is taken from a request:\n" + "\n".join(offenders)
    )


def test_the_ledger_actor_scan_finds_the_append_sites() -> None:
    """R-0007 — if the scan matches nothing, it cannot fail on a bad actor=."""
    hits = _ledger_actor_kwargs()
    assert len(hits) >= 2, (
        "the ledger-actor scan found almost no append sites, so a green result "
        f"means nothing. Found {hits}"
    )


def test_gold_metric_ctors_in_api_do_not_copy_attestation_from_the_request() -> None:
    hits = _gold_metric_ctors()
    offenders: list[str] = []
    for rel, lineno, call in hits:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        for field in ("signature", "signed_at", "ledger_entry_id"):
            if field in kw:
                offenders.append(f"{rel}:{lineno} copies {field} onto GoldMetricDef")
        steward = kw.get("steward_id")
        if steward is not None and _is_request_sourced(steward):
            offenders.append(f"{rel}:{lineno} steward_id is request-sourced")
        if steward is not None and isinstance(steward, ast.Name) and steward.id != "actor":
            offenders.append(
                f"{rel}:{lineno} steward_id={steward.id!r} (must be the server-resolved actor)"
            )
    assert not offenders, "GoldMetricDef in apps/api copies caller attestation:\n" + "\n".join(
        offenders
    )


def test_the_gold_metric_ctor_scan_finds_the_api_sites() -> None:
    """R-0007."""
    hits = _gold_metric_ctors()
    assert len(hits) >= 1, (
        "the GoldMetricDef scan found no constructors in apps/api, so a green "
        f"result means nothing. Found {hits}"
    )

