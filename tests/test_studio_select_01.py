"""STUDIO-SELECT-01 (dms#364): Studio table/column pick -> Cortex /ask payload.

The pick is packed as ``studio_selection`` (schema + ontology joins, no rows)
next to the question. Empty or unknown picks refuse by name and never widen to
the whole Space. A caller that sends no selection puts the same bytes on the
wire as before.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from cortex_client import CortexClient
from cortex_client.models import AskRequest
from dms_core.ask import SelectionRefused
from dms_executor import Executor
from dms_executor.manifest import ManifestMinter
from dms_executor.ontology import Ontology
from dms_executor.studio_selection import (
    SelectionAskRequest,
    build_studio_selection,
    read_selection_schema,
)

from tests.test_live_ask import FakeCortex, _live_client
from tests.test_live_ask import minter as minter  # noqa: F401 - pytest fixture

SCHEMA: dict[str, dict[str, str]] = {
    "transactions": {"txn_id": "VARCHAR", "sku": "VARCHAR", "location_id": "VARCHAR",
                     "quantity_kg": "DOUBLE"},
    "locations": {"location_id": "VARCHAR", "name": "VARCHAR"},
    "inventory": {"sku": "VARCHAR", "location_id": "VARCHAR", "category": "VARCHAR"},
    "suppliers": {"supplier_id": "VARCHAR"},
}


def _onto() -> Ontology:
    o = Ontology()
    o.add_object("transaction", "(SELECT *, CAST(ts AS DATE) AS day FROM transactions)",
                 ["txn_id"])
    o.add_object("location", "locations", ["location_id"])
    o.add_object("lot", "inventory", ["sku", "location_id"])
    o.add_object("supplier", "suppliers", ["supplier_id"])
    o.add_object("product",
                 "(SELECT sku, ANY_VALUE(category) AS category FROM inventory GROUP BY sku)",
                 ["sku"])
    o.add_link("txn_at_location", "transaction", ["location_id"], "location", ["location_id"])
    o.add_link("txn_of_product", "transaction", ["sku"], "product", ["sku"])
    o.add_link("txn_of_lot", "transaction", ["sku"], "lot", ["sku"])
    o.links["txn_of_lot"] = replace(o.links["txn_of_lot"], cardinality="many_to_many")
    o.verified = True
    return o


def _build(selection: list[dict[str, Any]]) -> dict[str, Any]:
    return build_studio_selection(selection, schema=SCHEMA, ontology=_onto())


# -- payload builder ---------------------------------------------------------


def test_selection_packs_only_selected_columns_with_types() -> None:
    out = _build([{"table": "transactions", "columns": ["sku", "quantity_kg"]}])
    assert out["tables"] == [
        {"table": "transactions",
         "columns": [{"name": "sku", "type": "VARCHAR"}, {"name": "quantity_kg", "type": "DOUBLE"}]}
    ]
    assert out["joins"] == [] and out["joins_omitted"] == []
    assert out["ontology_verified"] is True


def test_join_listed_only_between_selected_tables_with_keys_selected() -> None:
    out = _build([
        {"table": "transactions", "columns": ["location_id", "quantity_kg"]},
        {"table": "locations", "columns": ["location_id", "name"]},
    ])
    assert out["joins"] == [{
        "name": "txn_at_location",
        "from_table": "transactions", "from_columns": ["location_id"],
        "to_table": "locations", "to_columns": ["location_id"],
        "cardinality": "unverified",
    }]
    # suppliers is not selected: nothing about it reaches the payload.
    assert "suppliers" not in json.dumps(out)


def test_join_to_an_unselected_table_is_not_sent() -> None:
    out = _build([{"table": "locations", "columns": ["location_id"]}])
    assert out["joins"] == [] and out["joins_omitted"] == []


def test_join_whose_key_is_not_ticked_is_named_not_sent() -> None:
    out = _build([
        {"table": "transactions", "columns": ["quantity_kg"]},
        {"table": "locations", "columns": ["name"]},
    ])
    assert out["joins"] == []
    assert out["joins_omitted"] == [
        {"name": "txn_at_location", "reason": "key_columns_not_selected"}
    ]
    assert "location_id" not in json.dumps(out), "an unticked column reached the payload"


def test_many_to_many_and_derived_links_are_not_offered_as_joins() -> None:
    """txn_of_lot fans out (~15x); txn_of_product is a GROUP BY view, not inventory rows."""
    out = _build([
        {"table": "transactions", "columns": ["sku"]},
        {"table": "inventory", "columns": ["sku", "category"]},
    ])
    assert out["joins"] == []
    assert out["joins_omitted"] == [{"name": "txn_of_lot", "reason": "many_to_many"}]


def test_no_ontology_means_no_joins_and_says_so() -> None:
    out = build_studio_selection(
        [{"table": "locations", "columns": ["name"]}], schema=SCHEMA, ontology=None
    )
    assert out["joins"] == [] and out["ontology_verified"] is False


@pytest.mark.parametrize(
    ("selection", "code", "names"),
    [
        ([], "selection_empty", []),
        ([{"table": "nope", "columns": ["x"]}], "selection_unknown_table", ["nope"]),
        ([{"table": "transactions", "columns": ["sku", "ghost"]}],
         "selection_unknown_column", ["transactions.ghost"]),
        ([{"table": "transactions", "columns": []}], "selection_no_columns", ["transactions"]),
    ],
)
def test_empty_or_unknown_selection_is_a_named_refusal(
    selection: list[dict[str, Any]], code: str, names: list[str]
) -> None:
    with pytest.raises(SelectionRefused) as caught:
        _build(selection)
    assert caught.value.code == code
    assert caught.value.names == names


def test_schema_reader_returns_types_and_no_rows(tmp_path: Path) -> None:
    import duckdb

    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (a INTEGER, b VARCHAR)")
    con.execute("INSERT INTO t VALUES (42, 'secret-row-value')")
    con.close()
    got = read_selection_schema(db, ["t", "missing"])
    assert got == {"t": {"a": "INTEGER", "b": "VARCHAR"}}
    assert "secret-row-value" not in json.dumps(got)


def test_ungranted_table_refuses_like_a_missing_one() -> None:
    """``alerts`` exists but no Space grants it (A-0007): same code as a typo."""
    finance = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    with pytest.raises(SelectionRefused) as caught:
        Executor(cortex=None).studio_selection(
            [{"table": "alerts", "columns": ["alert_id"]}], finance
        )
    assert caught.value.code == "selection_unknown_table"
    assert caught.value.names == ["alerts"]


# -- wire: additive, existing callers unchanged ----------------------------


def _wire_body(req: AskRequest) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"answer": "ok", "audit_id": "a", "route": "sql",
                  "provenance": {"layer": "L0", "badge": "certified"}},
        )

    client = CortexClient("http://cortex.test")
    client._client.set_httpx_client(
        httpx.Client(base_url="http://cortex.test", transport=httpx.MockTransport(handler))
    )
    client.ask(req)
    return seen[0]


def test_no_selection_wire_body_is_unchanged() -> None:
    body = _wire_body(AskRequest(question="q", session_id="s", space_id="sp"))
    assert body == {"question": "q", "session_id": "s", "space_id": "sp", "tenant_id": None}


def test_selection_rides_the_wire_as_one_additive_key() -> None:
    sel = _build([{"table": "locations", "columns": ["name"]}])
    body = _wire_body(
        SelectionAskRequest(question="q", session_id="s", space_id="sp", studio_selection=sel)
    )
    assert body.pop("studio_selection") == sel
    assert body == {"question": "q", "session_id": "s", "space_id": "sp", "tenant_id": None}


# -- live ask + HTTP ----------------------------------------------------------


def test_live_ask_sends_selection_and_narrows_manifest(minter: ManifestMinter) -> None:  # noqa: F811
    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    exe.live_ask(
        "Top 5 selling SKUs by revenue",
        session_id="ses_sel",
        selection=[{"table": "transactions", "columns": ["sku", "quantity_kg"]}],
    )
    assert fake.asks, "the question never reached Cortex ask"
    req = fake.asks[-1]
    assert isinstance(req, SelectionAskRequest)
    tables = req.studio_selection["tables"]
    assert [t["table"] for t in tables] == ["transactions"]
    assert [c["name"] for c in tables[0]["columns"]] == ["sku", "quantity_kg"]
    assert "rows" not in json.dumps(req.studio_selection)
    bound = fake.submits[0].manifest.row_predicates
    assert set(bound) == {"transactions"}, "manifest was not narrowed to the selection"


def test_live_ask_without_selection_sends_plain_ask(minter: ManifestMinter) -> None:  # noqa: F811
    fake = FakeCortex(submits=[], asks=[])
    exe = Executor(cortex=fake, minter=minter)  # type: ignore[arg-type]
    exe.live_ask("Top 5 selling SKUs by revenue", session_id="ses_plain")
    assert type(fake.asks[-1]) is AskRequest


def test_http_unknown_column_is_400_named_and_reaches_no_cortex(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    fake = FakeCortex(submits=[], asks=[])
    client = _live_client(minter, monkeypatch, fake)
    r = client.post("/v1/chat/ask", json={
        "question": "Top 5", "session_id": "ses_h",
        "selection": [{"table": "transactions", "columns": ["ghost"]}],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "selection_unknown_column"
    assert r.json()["detail"]["names"] == ["transactions.ghost"]
    assert fake.asks == [] and fake.submits == []


def test_http_empty_selection_is_refused_not_widened(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    fake = FakeCortex(submits=[], asks=[])
    client = _live_client(minter, monkeypatch, fake)
    r = client.post("/v1/chat/ask", json={"question": "Top 5", "selection": []})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "selection_empty"
    assert fake.asks == [] and fake.submits == []


def test_http_selection_and_grounded_tables_together_is_refused(
    minter: ManifestMinter, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    fake = FakeCortex(submits=[], asks=[])
    client = _live_client(minter, monkeypatch, fake)
    r = client.post("/v1/chat/ask", json={
        "question": "Top 5", "grounded_tables": ["transactions"],
        "selection": [{"table": "transactions", "columns": ["sku"]}],
    })
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "selection_with_grounded_tables"


def test_ui_mounts_selector_and_posts_selection() -> None:
    """CI runs no vitest; this keeps the Studio wiring on the pytest job."""
    ui = Path(__file__).resolve().parents[1] / "apps" / "ui" / "src"
    page = (ui / "pages" / "StudioPage.tsx").read_text(encoding="utf-8")
    assert "<DataSelector" in page
    # The selection answer reaches the #365 ResultView through the same state key.
    assert "state: { studioEnvelope: env }" in page
    assert "<ResultView envelope={studioEnvelope} />" in page
    api = (ui / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "selection: payload.selection" in api
    sel = (ui / "components" / "studio" / "DataSelector.tsx").read_text(encoding="utf-8")
    assert "postAsk(" in sel and "selection" in sel
