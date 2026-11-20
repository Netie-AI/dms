"""PHONE-PATH-01 (#513): the stranger path on a phone.

Three things live here.

1. ``check_capture``: what ``apps/ui/e2e/phone-stranger-path.spec.ts`` runs on the
   ``POST /v1/chat/ask`` envelope it captured at 390x844 (B2, rule 10a). The spec
   calls ``python tests/test_phone_path_01.py <capture.json>``. The tests below
   prove the check can fail.
2. The picker pin (B5): every file kind the Studio picker offers is ingested by
   the ingest route, and ``.tsv`` is not offered.
3. A ``.tsv`` sent through the ingest route comes back not ingested, with a reason.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
STUDIO_PAGE = ROOT / "apps" / "ui" / "src" / "pages" / "StudioPage.tsx"
SPEC = ROOT / "apps" / "ui" / "e2e" / "phone-stranger-path.spec.ts"
FIXTURES = ROOT / "tests" / "fixtures" / "ingest"
SPACE = "11111111-2222-3333-4444-555555555555"
TABLE = "bronze.e2e_stranger_1"


def check_capture(capture: dict[str, Any]) -> None:
    """B2 on a captured envelope. Raises AssertionError with the clause that failed."""
    from dms_executor.envelope import assert_envelope_valid

    env = capture["envelope"]
    space_id = capture["space_id"]
    tables = set(capture["upload_tables"])
    assert tables, "B2: the capture names no uploaded table"
    assert_envelope_valid(env)
    assert env.get("ask_mode") == "live", (
        f"B2: ask_mode={env.get('ask_mode')!r}; a demo-mode answer never satisfies B2"
    )
    assert env.get("demo_fallback_used") is not True, "B2: demo fallback was used"
    assert env.get("space_id") == space_id, (
        f"B2: envelope space_id={env.get('space_id')!r}, the upload went to {space_id!r}"
    )
    sources = env.get("contributing_sources") or []
    if not env.get("abstained"):
        assert sources, "B2: a non-abstained answer cites no source"
    foreign = [s.get("container") for s in sources if s.get("container") not in tables]
    assert not foreign, f"B2: sources not created by the upload: {foreign}"


def _abstain() -> dict[str, Any]:
    """Shape of the envelope the phone received on 2026-11-20 (live, no OpenVault)."""
    return {
        "space_id": SPACE,
        "upload_tables": [TABLE],
        "envelope": {
            "answer_id": "ans_ov_mint",
            "text": "I can't answer this: DMS could not get its OpenVault signing key.",
            "values": [],
            "badge": "ABSTAIN",
            "abstained": True,
            "sql_used": None,
            "contributing_sources": [],
            "drillthrough_token": None,
            "audit_id": "ans_ov_mint",
            "ask_mode": "live",
            "demo_fallback_used": False,
            "space_id": SPACE,
            "rows": [],
            "as_of": "2026-11-20T01:34:51Z",
            "audit_receipt": {
                "include": {"status": "na", "row_count": 0, "rows": [], "why": "N/A: abstained"},
                "exclude": {"status": "na", "reasons": [], "why": "N/A: abstained"},
                "unsure": {"status": "abstain", "abstained": True, "why": "no signing key"},
            },
        },
    }


def _answer() -> dict[str, Any]:
    cap = _abstain()
    cap["envelope"].update(
        text="Total qty is 66.",
        values=[{"id": "v1", "value": 66, "label": "total qty"}],
        badge="L2_VALIDATED",
        abstained=False,
        sql_used=f"SELECT SUM(qty) AS total_qty FROM {TABLE}",
        contributing_sources=[{"ref_id": "r1", "container": TABLE, "kind": "sql"}],
        drillthrough_token="tok_12345678",
        audit_id="ans_1",
        rows=[{"total_qty": 66}],
        audit_receipt={
            "include": {"status": "rows", "row_count": 1, "rows": [{"total_qty": 66}], "why": "1 row"},
            "exclude": {"status": "na", "reasons": [], "why": "no filter"},
            "unsure": {"status": "none", "abstained": False, "why": "answered"},
        },
    )
    return cap


def test_check_accepts_a_truthful_abstain_and_a_grounded_answer() -> None:
    check_capture(_abstain())
    check_capture(_answer())


def _mutate(base: dict[str, Any], **env: Any) -> dict[str, Any]:
    cap = copy.deepcopy(base)
    cap["envelope"].update(env)
    return cap


@pytest.mark.parametrize(
    ("capture", "clause"),
    [
        (_mutate(_abstain(), badge="L0_CERTIFIED"), "E1"),
        (_mutate(_abstain(), values=[{"id": "v", "value": 1}]), "E2"),
        (_mutate(_abstain(), ask_mode="demo"), "demo-mode"),
        (_mutate(_abstain(), demo_fallback_used=True), "E6|demo fallback"),
        (_mutate(_abstain(), space_id="cccccccc-cccc-cccc-cccc-cccccccccccc"), "space_id"),
        (_mutate(_answer(), space_id=None), "space_id"),
        (_mutate(_answer(), contributing_sources=[]), "cites no source"),
        (
            _mutate(
                _answer(),
                contributing_sources=[{"ref_id": "r", "container": "transactions", "kind": "sql"}],
            ),
            "not created by the upload",
        ),
        (_mutate(_answer(), sql_used=None), "E3"),
        (_mutate(_answer(), text="Total qty is 66 and revenue is RM 9,999.50."), "E4"),
        ({**_answer(), "upload_tables": []}, "names no uploaded table"),
    ],
)
def test_check_refuses(capture: dict[str, Any], clause: str) -> None:
    with pytest.raises(AssertionError, match=clause):
        check_capture(capture)


def test_spec_never_rewrites_a_response() -> None:
    """B2 and B4 read the envelope the API sent. No route interception in the spec."""
    src = SPEC.read_text(encoding="utf-8")
    assert ".route(" not in src
    assert ".fulfill(" not in src
    assert "setInputFiles" not in src, "files go through the chooser the tapped control opens"
    assert "filechooser" in src
    for banned in ("test.skip", "test.fixme", "test.fail(", ".only("):
        assert banned not in src, f"{banned} in the stranger-path spec"


def _offered_kinds() -> list[str]:
    """The ``accept`` list of the Studio file picker, read from the page source."""
    src = STUDIO_PAGE.read_text(encoding="utf-8")
    accepts = re.findall(r'<input\b[^>]*?\baccept="([^"]*)"', src, flags=re.S)
    assert len(accepts) == 1, f"expected one file picker with an accept list, found {accepts}"
    return [k.strip().lower() for k in accepts[0].split(",") if k.strip()]


def _payload(kind: str) -> tuple[str, bytes]:
    if kind == ".csv":
        return "e2e_kind.csv", b"sku,qty,region\nA,11,North\nB,22,South\n"
    if kind in (".xlsx", ".xlsm"):
        # .xlsm is the same container as .xlsx; the fixture carries no macro.
        return f"e2e_kind{kind}", (FIXTURES / "15_q3_sales_export.xlsx").read_bytes()
    raise AssertionError(
        f"the picker offers {kind!r} and this test has no file of that kind: "
        "add one here with proof that the ingest route lands it"
    )


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import dms_api.routes.studio as studio_routes
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from dms_executor import demo_warehouse as dw
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "phone_path.duckdb"))
    dw._SEEDED.clear()
    dw.ensure_demo_warehouse(tmp_path / "phone_path.duckdb")
    get_settings.cache_clear()
    monkeypatch.setattr(
        studio_routes,
        "compliance_gate",
        lambda *, action, **_: ComplianceDecision(allowed=True, reason="test_allow", action=action),
    )
    return TestClient(create_app())


def test_picker_does_not_offer_tsv() -> None:
    kinds = _offered_kinds()
    assert kinds, "the picker has no accept list"
    assert ".tsv" not in kinds


def test_every_kind_the_picker_offers_is_ingested(client: Any) -> None:
    for kind in _offered_kinds():
        name, data = _payload(kind)
        r = client.post("/v1/studio/ingest", files={"file": (name, data, "application/octet-stream")})
        assert r.status_code == 200, f"{kind}: {r.status_code} {r.text}"
        body = r.json()
        assert body["ingested"] >= 1, f"{kind} is offered by the picker and not ingested: {body}"
        landed = [f for f in body["files"] if f["ingested"]]
        assert landed and all(f["table"] for f in landed), f"{kind}: no table on the receipt: {body}"


TSV = b"sku\tqty\tregion\nA\t11\tNorth\nB\t22\tSouth\n"
# A tab-separated file whose cells hold commas reads as several columns to a comma parser.
TSV_WITH_COMMAS = b"sku,code\tqty,kg\tregion,zone\nA,1\t11,0\tNorth,1\nB,2\t22,0\tSouth,2\n"


@pytest.mark.parametrize("data", [TSV, TSV_WITH_COMMAS], ids=["plain", "commas-in-cells"])
def test_tsv_through_the_ingest_route_is_not_ingested_and_says_why(client: Any, data: bytes) -> None:
    r = client.post("/v1/studio/ingest", files={"file": ("e2e_kind.tsv", data, "text/tab-separated-values")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingested"] == 0
    assert body["need_attention"] == 1
    assert body["chat_can_see_it"] is False
    (row,) = body["files"]
    assert row["file"] == "e2e_kind.tsv"
    assert row["ingested"] is False
    assert row["table"] is None
    assert row["reason"], "a refused file carries its reason"
    assert "0 ingested" in body["summary"] and "1 need attention" in body["summary"]


def test_tsv_in_a_batch_does_not_stop_the_csv_beside_it(client: Any) -> None:
    r = client.post(
        "/v1/studio/ingest-batch",
        files=[
            ("files", ("e2e_kind.tsv", TSV, "text/tab-separated-values")),
            ("files", ("e2e_kind.csv", b"sku,qty\nA,1\nB,2\n", "text/csv")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_file = {f["file"]: f for f in body["files"]}
    assert by_file["e2e_kind.tsv"]["ingested"] is False
    assert by_file["e2e_kind.tsv"]["table"] is None
    assert by_file["e2e_kind.tsv"]["reason"]
    assert by_file["e2e_kind.csv"]["ingested"] is True
    assert (body["ingested"], body["need_attention"]) == (1, 1)


if __name__ == "__main__":
    check_capture(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    print("PHONE-PATH-01 B2 envelope check: OK")
