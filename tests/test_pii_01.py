"""PII-01 / dms#272: mask personal columns before Cortex context and in exports.

Seeded fake table. Capture everything handed to compute (the Cortex/model
context) plus export bytes. Raw seeded values must not appear. Counts over
those columns still answer. DMSMASK_* placeholders are not rewritten by a
plain regex PII masker (Cortex #268 stand-in). Live both-maskers run is leftover.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from cortex_client.compute import INSIGHTS_PATH, compute_insights
from dms_core.bi_export import export_envelope_bi
from dms_core.pii import (
    MASK_TOKEN_RE,
    classify_column,
    column_is_pii,
    fail_closed_mask_envelope,
    is_mask_token,
    mask_payload,
    sanitize_retrieve_parts,
)
from dms_core.xlsx_export import export_envelope_xlsx
from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
from dms_executor.generative_ask import maybe_generative_ask
from dms_executor.ontology import Ontology
from dms_executor.semantic_retrieve import retrieve_short_context, retrieve_value_encodings

# Distinctive fakes. Must not appear in compute context, envelope, or exports.
NAME_1 = "Aisha Zulkifli"
NAME_2 = "Ravi Chandran"
EMAIL_1 = "aisha.zulkifli@pii-seed.example"
EMAIL_2 = "ravi.chandran@pii-seed.example"
PHONE_1 = "+60135550101"
PHONE_2 = "012-35550102"
NRIC_1 = "900101-14-5678"
NRIC_2 = "850505101234"
ACCOUNT_1 = "123-456-789012"
ACCOUNT_2 = "1111222233334444"
SEEDED_RAW = (
    NAME_1,
    NAME_2,
    EMAIL_1,
    EMAIL_2,
    PHONE_1,
    PHONE_2,
    NRIC_1,
    NRIC_2,
    ACCOUNT_1,
    ACCOUNT_2,
)

GRANTS = {"customers"}
LIST_Q = (
    "Show customer_name email phone ic_number account_no and region "
    "for each customer"
)
COUNT_Q = "How many customers?"


def _plain_regex_pii_masker(text: str) -> str:
    """Cortex #268 regex kinds only (not NER names). Not the live Cortex masker."""
    out = text
    out = re.sub(
        r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", "<PII:EMAIL>", out, flags=re.I
    )
    out = re.sub(r"\b\d{6}-?\d{2}-?\d{4}\b", "<PII:IC>", out)
    out = re.sub(
        r"(?:\+?60\s*1\d[-\s]?\d{7,8}|01\d[-\s]?\d{7,8}|\+[1-9]\d{9,14})",
        "<PII:PHONE>",
        out,
    )
    out = re.sub(r"(?<!\d)\d{10,16}(?!\d)", "<PII:ACCOUNT>", out)
    return out


def _blob(*objs: object) -> str:
    return json.dumps(objs, default=str)


def _xlsx_inner(data: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        return b"".join(zf.read(n) for n in zf.namelist())


def _assert_no_seeded(blob: str | bytes, *, where: str) -> None:
    if isinstance(blob, bytes):
        hay = _xlsx_inner(blob) if blob[:2] == b"PK" else blob
        for raw in SEEDED_RAW:
            assert raw.encode("utf-8") not in hay, f"{where} leaked {raw!r}"
        return
    for raw in SEEDED_RAW:
        assert raw not in blob, f"{where} leaked {raw!r}"


def _ontology() -> Ontology:
    o = Ontology()
    o.add_object("customer", "customers", ["customer_id"])
    o.add_measure("customer_count", "customer", "COUNT(*)")
    o.add_measure("revenue", "customer", "SUM(f.amount_myr)")
    return o


def _seed(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE customers ("
            "customer_id INTEGER, customer_name VARCHAR, email VARCHAR, "
            "phone VARCHAR, ic_number VARCHAR, account_no VARCHAR, "
            "region VARCHAR, amount_myr DOUBLE)"
        )
        con.execute(
            "INSERT INTO customers VALUES "
            f"(1, '{NAME_1}', '{EMAIL_1}', '{PHONE_1}', '{NRIC_1}', "
            f"'{ACCOUNT_1}', 'North', 100),"
            f"(2, '{NAME_2}', '{EMAIL_2}', '{PHONE_2}', '{NRIC_2}', "
            f"'{ACCOUNT_2}', 'South', 200),"
            "(3, 'Warehouse Ops Desk', 'ops@internal.local', '03-11112222', "
            "'not-an-ic', 'AB12', 'North', 300)"
        )
    finally:
        con.close()


def _submitter(path: Path) -> Any:
    def submit(sql: str) -> Any:
        con = duckdb.connect(str(path))
        try:
            cur = con.execute(sql)
            cols = [str(c[0]) for c in (cur.description or [])]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            return SimpleNamespace(ok=False, status="err", run_id="run_pii", output=None)
        finally:
            con.close()
        return SimpleNamespace(ok=True, status="ok", run_id="run_pii", output={"rows": rows})

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    return SimpleNamespace(entry_id="led_pii", hash="hash_pii_not_entry")


def test_column_name_and_value_patterns() -> None:
    assert classify_column("customer_name", ()) == "name"
    assert classify_column("email", ()) == "email"
    assert classify_column("phone", ()) == "phone"
    assert classify_column("ic_number", ()) == "nric"
    assert classify_column("nric", ()) == "nric"
    assert classify_column("account_no", ()) == "account"
    assert classify_column("notes", [EMAIL_1]) == "email"
    assert classify_column("notes", [NRIC_1]) == "nric"
    assert classify_column("notes", [PHONE_1]) == "phone"
    assert classify_column("name", (), table="customers") == "name"
    assert classify_column("name", ["Warehouse A"], table="locations") is None
    assert classify_column("supplier_name", ["Northshore Materials"]) is None
    assert classify_column("region", ["North", "South"]) is None
    assert classify_column("customer_code", ["C1"]) is None
    assert column_is_pii("email", ()) is True
    assert column_is_pii("sku", ["SKU-ALPHA"]) is False


def test_old_retrieve_skip_regex_does_not_catch_pii_names() -> None:
    skip = re.compile(
        r"(amount|qty|quantity|cost|kg|myr|score|load|capacity|date|id)$", re.I
    )
    for col in ("customer_name", "email", "phone", "ic_number", "nric", "account_no"):
        assert skip.search(col) is None, col


def test_placeholders_are_stable_and_not_pii_shaped() -> None:
    got = mask_payload(
        text=f"{NAME_1} {EMAIL_1} {PHONE_1} {NRIC_1} {ACCOUNT_1}",
        rows=[{"customer_name": NAME_1, "email": EMAIL_1, "n": 3}],
    )
    text = got["text"]
    row = got["rows"][0]
    assert is_mask_token(row["customer_name"])
    assert is_mask_token(row["email"])
    assert row["n"] == 3
    assert MASK_TOKEN_RE.search(text)
    _assert_no_seeded(text, where="masked text")
    _assert_no_seeded(_blob(got["rows"]), where="masked rows")
    again = mask_payload(text=text, rows=got["rows"])
    assert again["text"] == text
    assert again["rows"][0]["email"] == row["email"]
    assert _plain_regex_pii_masker(text) == text
    assert _plain_regex_pii_masker(row["email"]) == row["email"]
    assert "@" not in row["email"]
    assert not re.search(r"\d{6}-?\d{2}-?\d{4}", row.get("customer_name", ""))


def test_seeded_values_never_reach_compute_or_export(tmp_path: Path) -> None:
    lake = tmp_path / "pii.duckdb"
    _seed(lake)
    captured: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        captured.append(ctx)
        return {
            "query_sql": (
                "SELECT customer_name, email, phone, ic_number, account_no, "
                "region, amount_myr FROM customers"
            )
        }

    env = maybe_generative_ask(
        LIST_Q,
        warehouse=lake,
        grantable=set(GRANTS),
        compute=compute,
        submit=_submitter(lake),
        ledger_append=_ledger,
        ontology=_ontology(),
    )
    assert env is not None
    assert_envelope_valid(env)
    assert captured, "compute never ran"
    ctx_blob = _blob(captured)
    _assert_no_seeded(ctx_blob, where="compute context")
    encodings = captured[0].get("encodings") or {}
    for key in encodings:
        low = str(key).lower()
        assert "email" not in low
        assert "phone" not in low
        assert "ic_number" not in low
        assert "account_no" not in low
        assert "customer_name" not in low

    _assert_no_seeded(str(env.get("text") or ""), where="envelope text")
    _assert_no_seeded(_blob(env.get("rows"), env.get("values")), where="envelope rows")
    _assert_no_seeded(_blob(env), where="envelope json")
    assert env["abstained"] is False, env.get("text")
    assert env["rows"], env
    nums = [
        float(v)
        for row in env["rows"]
        for v in row.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert 100.0 in nums and 200.0 in nums

    xlsx, _name = export_envelope_xlsx(env)
    _assert_no_seeded(xlsx, where="xlsx export")
    bi = export_envelope_bi(env)
    _assert_no_seeded(_blob(bi), where="bi export")
    raw_env = dict(env)
    raw_env["rows"] = [{"email": EMAIL_1, "qty": 1}]
    raw_env["text"] = f"email={EMAIL_1}"
    x2, _n2 = export_envelope_xlsx(raw_env)
    _assert_no_seeded(x2, where="xlsx of tampered envelope")
    b2 = export_envelope_bi(raw_env)
    _assert_no_seeded(_blob(b2), where="bi of tampered envelope")


def test_count_over_pii_column_still_answers(tmp_path: Path) -> None:
    lake = tmp_path / "pii_count.duckdb"
    _seed(lake)
    captured: list[dict[str, Any]] = []

    def compute(ctx: dict[str, Any]) -> dict[str, Any]:
        captured.append(ctx)
        return {"query_sql": "SELECT COUNT(email) AS n FROM customers"}

    env = maybe_generative_ask(
        COUNT_Q,
        warehouse=lake,
        grantable=set(GRANTS),
        compute=compute,
        submit=_submitter(lake),
        ledger_append=_ledger,
        ontology=_ontology(),
    )
    assert env is not None
    assert_envelope_valid(env)
    _assert_no_seeded(_blob(captured, env), where="count path")
    assert env["abstained"] is False, env.get("text")
    nums = [
        float(v)
        for row in (env.get("rows") or [])
        for v in row.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    assert 3.0 in nums, env.get("rows")
    assert "3" in str(env.get("text") or "")


def test_retrieve_drops_pii_encodings(tmp_path: Path) -> None:
    lake = tmp_path / "pii_enc.duckdb"
    _seed(lake)
    ctx = retrieve_short_context(
        LIST_Q, warehouse=lake, grantable=set(GRANTS), ontology=_ontology()
    )
    _assert_no_seeded(_blob(ctx), where="retrieve_short_context")
    enc = retrieve_value_encodings(
        lake,
        [
            {
                "table": "customers",
                "columns": [
                    "customer_name",
                    "email",
                    "phone",
                    "ic_number",
                    "account_no",
                    "region",
                ],
            }
        ],
        {
            "customer",
            "name",
            "email",
            "phone",
            "ic",
            "number",
            "account",
            "no",
            "region",
        },
    )
    _assert_no_seeded(_blob(enc), where="retrieve_value_encodings")
    assert "customers.email" not in enc
    assert "customers.customer_name" not in enc
    assert enc.get("customers.region")


def test_insights_ontology_body_has_no_seeded_values(tmp_path: Path) -> None:
    """GEN-RESTORE: retrieve ctx is compute_insights ontology then body['ontology']."""
    lake = tmp_path / "pii_insights.duckdb"
    _seed(lake)
    posts: list[dict[str, Any]] = []

    class _CaptureHttp:
        def __init__(self, *a: Any, timeout: Any = None, **k: Any) -> None:
            return None

        def __enter__(self) -> _CaptureHttp:
            return self

        def __exit__(self, *a: Any) -> None:
            return None

        def post(
            self,
            url: str,
            json: dict[str, Any] | None = None,
            headers: Any = None,
        ) -> Any:
            posts.append({"url": url, "json": json or {}})

            class _Resp:
                status_code = 200

                def json(self) -> dict[str, Any]:
                    return {
                        "phase": "generate",
                        "generative": {"ok": False, "sql": None},
                    }

            return _Resp()

        def get(
            self,
            url: str,
            params: Any = None,
            headers: Any = None,
        ) -> Any:
            posts.append({"url": url, "json": params or {}})

            class _Resp:
                status_code = 200

                def json(self) -> dict[str, Any]:
                    return {}

            return _Resp()

    def compute(catalog: dict[str, Any]) -> dict[str, Any] | None:
        return compute_insights(
            "http://127.0.0.1:8010",
            question=LIST_Q,
            ontology=catalog,
            api_key="fake-key01-test-token",
        )

    with patch("cortex_client.compute.httpx.Client", _CaptureHttp):
        maybe_generative_ask(
            LIST_Q,
            warehouse=lake,
            grantable=set(GRANTS),
            compute=compute,
            submit=_submitter(lake),
            ledger_append=_ledger,
            ontology=_ontology(),
        )

    bodies = [
        p["json"]
        for p in posts
        if str(p.get("url") or "").endswith(INSIGHTS_PATH)
        and isinstance(p.get("json"), dict)
    ]
    assert bodies, posts
    for body in bodies:
        assert "ontology" in body, list(body)
        _assert_no_seeded(_blob(body), where="insights POST body")
        _assert_no_seeded(_blob(body["ontology"]), where="insights body ontology")


def test_fail_closed_drops_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lake = tmp_path / "pii_fail.duckdb"
    _seed(lake)

    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("detector down")

    monkeypatch.setattr("dms_core.pii.classify_column", boom)
    enc = retrieve_value_encodings(
        lake,
        [{"table": "customers", "columns": ["email", "region"]}],
        {"email", "region"},
    )
    assert enc == {}
    out = sanitize_retrieve_parts(
        {"encodings": {"customers.email": [EMAIL_1]}, "bound_values": {"x": EMAIL_1}}
    )
    assert EMAIL_1 not in _blob(out)


def test_sanitize_wipes_encodings_when_drop_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_enc: object) -> dict[str, list[str]]:
        raise RuntimeError("drop failed")

    monkeypatch.setattr("dms_core.pii.drop_pii_encodings", boom)
    out = sanitize_retrieve_parts(
        {"encodings": {"customers.email": [EMAIL_1]}, "bound_values": {"region": "North"}}
    )
    assert out["encodings"] == {}
    assert EMAIL_1 not in _blob(out)


def test_warehouse_name_is_not_masked() -> None:
    env = build_answer_envelope(
        answer_id="ans_wh",
        text="Found 1 row(s).\n  - name=Warehouse A, capacity_kg=100000.0",
        badge="L2_VALIDATED",
        abstained=False,
        rows=[{"name": "Warehouse A", "capacity_kg": 100000.0}],
        sql_used="SELECT name, capacity_kg FROM locations",
        as_of="2026-09-25T00:00:00Z",
        audit_id="aud_wh",
        ask_mode="live",
        question="which warehouse",
    )
    assert_envelope_valid(env)
    assert env["rows"][0]["name"] == "Warehouse A"
    assert "Warehouse A" in env["text"]


def test_export_idempotent_on_dms_tokens() -> None:
    env = fail_closed_mask_envelope(
        {
            "answer_id": "ans_pii_tok",
            "badge": "L2_VALIDATED",
            "text": "one",
            "values": [{"id": "v0", "value": 1.0, "label": "n"}],
            "rows": [{"email": "DMSMASK_email_01", "n": 1}],
            "as_of": "2026-09-25T00:00:00Z",
            "sql_used": "SELECT 1",
            "audit_id": "aud_pii_tok",
            "assumptions": [],
            "contributing_sources": [],
        }
    )
    assert env["rows"][0]["email"] == "DMSMASK_email_01"
    data, _name = export_envelope_xlsx(env)
    assert b"DMSMASK_email_01" in _xlsx_inner(data)
    assert _plain_regex_pii_masker("DMSMASK_email_01") == "DMSMASK_email_01"
