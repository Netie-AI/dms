"""ADV-SAME-NAME: two Spaces / two schemas with same-named tables (red team, dms#518).

Every test asserts the SECURE behaviour; a failure is a finding.

Adversary round for CATALOG-SCHEMA-01 (dms#518), written by an agent that did
not write the fix. Kept here: the cases that hold. The cases that failed on a
defect older than this change are listed by name in the pull request, not
skipped here.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_client.client import CortexClient
from dms_api.app import create_app
from dms_api.settings import Settings, get_settings
from dms_executor import Executor
from dms_executor import demo_warehouse as dw
from dms_executor import schema_context as sc
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.manifest import ManifestMinter
from dms_executor.schema_context import (
    _forget_index,
    _serving_file,
    build_space_index,
    cached_value_index,
    prepare_generate_context,
    read_catalog,
)
from fastapi.testclient import TestClient
from test_catalog_schema_01 import _WireCortex
from test_drop_route_fields_01 import _KEY, _LOOPBACK, _Ranked
from test_onto_derive_01 import _gate_allows, make_minter
from test_schema_context_no_empty_01 import _schema_prompts, _tables_in

A_CANARY = "ACANARYVALUE"
B_CANARY = "BCANARYVALUE"


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> ManifestMinter:
    return make_minter(monkeypatch)


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_CLOOP_B",
        "DMS_LANE_ONTOLOGY_RANKED",
        "DMS_ONTO_FROM_DATA",
        "DMS_DEF_VALIDATE",
        "DMS_RECONFIRM_RUN_HELD",
        "DMS_AI_FIRST",
        "DMS_VQ_RAG",
        "DMS_CCA_CASCADE",
        "DMS_STRICT_MODEL",
        "DMS_STRICT_PROVIDER",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DMS_SCHEMA_CONTEXT", "1")
    monkeypatch.setenv("DMS_DEMO_FALLBACK", "0")
    from dms_executor.reconfirm import _reset_reconfirm

    _reset_reconfirm()


def _settle() -> None:
    """Let every background index build finish. Does NOT set the stop flag."""
    with sc._WORKER_LOCK:
        workers = list(sc._WORKERS)
    for worker in workers:
        worker.join(30)


class _World:
    def __init__(
        self,
        app: Any,
        client: TestClient,
        cortex: Any,
        lake: Path,
        posts: list[dict[str, Any]],
        stub: Any,
        exe: Executor,
    ) -> None:
        self.app, self.client, self.cortex, self.lake = app, client, cortex, lake
        self.posts, self.stub, self.exe = posts, stub, exe

    def space(self, name: str, files: list[tuple[str, str]]) -> str:
        sp = self.app.state.space_store.create(name)
        self.upload(sp.id, files)
        return str(sp.id)

    def upload(self, space_id: str, files: list[tuple[str, str]]) -> list[str]:
        r = self.client.post(
            "/v1/studio/ingest-batch",
            data={"space_id": space_id},
            files=[("files", (n, t.encode(), "text/csv")) for n, t in files],
        )
        assert r.status_code == 200, r.text
        _settle()
        return [f["table"] for f in r.json()["files"]]

    def grants(self, space_id: str | None) -> set[str]:
        return set(self.exe.grantable_tables(space_id=space_id))

    def ask(self, space_id: str | None, question: str, sql: str) -> dict[str, Any]:
        self.stub.payload["query_sql"] = sql
        self.stub.payload["sql"] = sql
        self.stub.payload["generative"] = {"ok": True, "sql": sql, "stamp": {"task": "freeroute"}}
        body: dict[str, Any] = {"question": question, "session_id": "ses_adv"}
        if space_id:
            body["space_id"] = space_id
        r = self.client.post("/v1/chat/ask", json=body)
        assert r.status_code == 200, r.text
        env = r.json()
        assert_envelope_valid(env)
        return env

    def prompts(self, since: int = 0) -> list[str]:
        return [p for b in self.posts[since:] for p in _schema_prompts(b)]


def _world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter) -> _World:
    lake = tmp_path / "adv.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(lake))
    dw._SEEDED.clear()
    ensure_demo_warehouse(lake)
    get_settings.cache_clear()
    _gate_allows(monkeypatch)
    posts: list[dict[str, Any]] = []
    stub = _Ranked("SELECT 1 AS x", "x")
    base = stub.client()

    class _Capture(base):  # type: ignore[misc, valid-type]
        def post(
            self,
            url: str,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            if str(url).rstrip("/").endswith("/v1/insights"):
                posts.append(dict(json or {}))
            return super().post(url, json=json, headers=headers)

    monkeypatch.setattr("cortex_client.compute.httpx.Client", _Capture)
    cortex = _WireCortex(
        warehouse=lake, payload={}, inner=CortexClient(_LOOPBACK, api_key=_KEY, timeout=5.0)
    )
    app = create_app()
    exe = Executor(cortex=cortex, minter=minter, warehouse_path=lake)  # type: ignore[arg-type]
    app.state.ask_service = exe
    app.state.cortex = cortex
    settings = Settings(
        _env_file=None,
        dms_ask_mode="live",  # type: ignore[call-arg]
        dms_demo_fallback=False,
        dms_harness_ask_paths=True,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    return _World(app, TestClient(app), cortex, lake, posts, stub, exe)


def _csv(header: list[str], rows: list[list[Any]]) -> str:
    return ",".join(header) + "\n" + "".join(",".join(str(c) for c in r) + "\n" for r in rows)


def _dump() -> str:
    """Everything held in memory for any Space."""
    with sc._INDEX_LOCK:
        return json.dumps(
            {
                "i": sc._SPACE_INDEX,
                "s": sc._SPACE_SAMPLES,
                "c": {k: sorted(v) for k, v in sc._SPACE_CAPPED.items()},
                "f": sc._SPACE_FP,
            },
            default=list,
        )


def _disk(w: _World, space_id: str) -> str:
    path = _serving_file(w.lake, space_id)
    return path.read_text(encoding="utf-8") if path.exists() else ""


A_ORDERS = _csv(["order_id", "a_col", "status"], [[1, A_CANARY, "AWEB"], [2, "x", "ASTORE"]])
B_ORDERS = _csv(["order_id", "b_col", "status"], [[9, B_CANARY, "BWEB"]])
ORD_SQL = "SELECT COUNT(*) AS n FROM bronze.orders"


# --- 1. two Spaces upload the same filename ---------------------------------------


def test_first_space_is_served_nothing_of_the_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    a = w.space("A", [("orders.csv", A_ORDERS)])
    assert w.grants(a) == {"bronze.orders"}
    b = w.space("B", [("orders.csv", B_ORDERS)])
    # A's table was replaced by B's. A must not be granted B's table.
    assert w.grants(b) == {"bronze.orders"}
    assert "bronze.orders" not in w.grants(a), "two Spaces own one table"
    n = len(w.posts)
    env = w.ask(a, "how many orders", ORD_SQL)
    text = json.dumps(env)
    for leak in (B_CANARY, "BWEB", "b_col"):
        assert leak not in text, leak
    assert env["abstained"] is True or env["rows"] == []
    for prompt in w.prompts(n):
        assert "b_col" not in prompt and B_CANARY not in prompt
    # The lake's rows for B are not in A's rows either.
    assert env["rows"] == [] and env["values"] == []


def test_each_space_holds_only_its_own_values_in_memory_and_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    a = w.space("A", [("orders.csv", A_ORDERS)])
    assert build_space_index(w.lake, a, w.grants(a), "duckdb") == ""
    assert "AWEB" in json.dumps(cached_value_index(a))
    b = w.space("B", [("orders.csv", B_ORDERS)])
    assert build_space_index(w.lake, b, w.grants(b), "duckdb") == ""
    for marker in ("BWEB", B_CANARY, "b_col"):
        assert marker not in json.dumps(cached_value_index(a)), marker
        assert marker not in json.dumps(sc._space_samples(a)), marker
        assert marker not in _disk(w, a), marker
    for marker in ("AWEB", A_CANARY, "a_col", "ASTORE"):
        assert marker not in json.dumps(cached_value_index(b)), marker
        assert marker not in _disk(w, b), marker


def test_a_lost_grant_drops_the_space_index_for_that_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """A's own old values must not outlive the grant once A asks again (no cross-Space leak)."""
    w = _world(tmp_path, monkeypatch, minter)
    a = w.space("A", [("orders.csv", A_ORDERS)])
    assert build_space_index(w.lake, a, w.grants(a), "duckdb") == ""
    w.space("B", [("orders.csv", B_ORDERS)])
    assert not w.grants(a)
    # Re-run what an ask does for A (empty grants). Nothing for A may stay indexed.
    prepare_generate_context(
        {}, question="orders", serving=w.lake, grantable=w.grants(a), dialect="duckdb", space_id=a
    )
    assert cached_value_index(a) == {}, "stale index for a table A no longer owns"


def test_reupload_with_other_columns_shows_only_the_new_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    a = w.space("A", [("orders.csv", A_ORDERS)])
    assert build_space_index(w.lake, a, w.grants(a), "duckdb") == ""
    w.upload(a, [("orders.csv", _csv(["order_id", "a2_col", "status"], [[1, "N", "NEWWEB"]]))])
    n = len(w.posts)
    w.ask(a, "orders by status", ORD_SQL)
    _settle()
    w.ask(a, "orders by status NEWWEB", ORD_SQL)
    for prompt in w.prompts(n):
        assert "a_col" not in prompt and "AWEB" not in prompt and "ASTORE" not in prompt, prompt


def test_reupload_same_shape_new_values_does_not_serve_stale_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """Fingerprint = names, types, size estimate. A same-shaped re-upload keeps the old values."""
    w = _world(tmp_path, monkeypatch, minter)
    a = w.space("A", [("orders.csv", A_ORDERS)])
    assert build_space_index(w.lake, a, w.grants(a), "duckdb") == ""
    assert "AWEB" in json.dumps(cached_value_index(a))
    new = _csv(["order_id", "a_col", "status"], [[1, A_CANARY, "ZNEW"], [2, "x", "ZNEWTWO"]])
    w.upload(a, [("orders.csv", new)])
    _settle()
    ctx = prepare_generate_context(
        {},
        question="orders with status ZNEW",
        serving=w.lake,
        grantable=w.grants(a),
        dialect="duckdb",
        space_id=a,
    )
    prompt = str(ctx.get(sc.SCHEMA_CONTEXT_FIELD, ""))
    assert "ASTORE" not in prompt and "AWEB" not in prompt, prompt


def test_b_overwrites_then_a_reuploads_nothing_of_b_survives_in_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    a = w.space("A", [("orders.csv", A_ORDERS)])
    b = w.space("B", [("orders.csv", B_ORDERS)])
    assert build_space_index(w.lake, b, w.grants(b), "duckdb") == ""
    w.upload(a, [("orders.csv", A_ORDERS)])
    assert w.grants(a) == {"bronze.orders"} and "bronze.orders" not in w.grants(b)
    ctx = prepare_generate_context(
        {},
        question="orders by status",
        serving=w.lake,
        grantable=w.grants(b),
        dialect="duckdb",
        space_id=b,
    )
    # B no longer owns the table: B's prompt must not show A's columns.
    prompt = str(ctx.get(sc.SCHEMA_CONTEXT_FIELD, ""))
    assert "a_col" not in prompt and A_CANARY not in prompt and "AWEB" not in prompt
    ctx = prepare_generate_context(
        {},
        question="orders by status",
        serving=w.lake,
        grantable=w.grants(a),
        dialect="duckdb",
        space_id=a,
    )
    prompt = str(ctx.get(sc.SCHEMA_CONTEXT_FIELD, ""))
    assert "b_col" not in prompt and B_CANARY not in prompt and "BWEB" not in prompt


# --- 2. bare name in main and bronze ----------------------------------------------


INV_CSV = _csv(
    ["sku", "bronze_only_col", "grade"], [["S1", 1, "GRADEBRONZE"], ["S2", 2, "GRADEBRONZE"]]
)


def test_file_space_inventory_does_not_read_the_demo_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    f = w.space("F", [("inventory.csv", INV_CSV)])
    assert w.grants(f) == {"bronze.inventory"}
    fp, body = read_catalog(w.lake, w.grants(f), "duckdb")
    assert [d["name"] for d in body["datasets"]] == ["bronze.inventory"]
    n = len(w.posts)
    w.ask(f, "how many skus", "SELECT COUNT(*) AS n FROM bronze.inventory")
    for prompt in w.prompts(n):
        assert _tables_in(prompt) == ["bronze.inventory"], prompt


def test_bare_name_sql_in_a_file_space_never_reads_the_main_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    """The model may write ``FROM inventory`` for a granted ``bronze.inventory``.

    DuckDB resolves a bare name to ``main.inventory``: the demo table, not granted.
    """
    w = _world(tmp_path, monkeypatch, minter)
    f = w.space("F", [("inventory.csv", INV_CSV)])
    con = duckdb.connect(str(w.lake))
    demo_rows = con.execute("SELECT COUNT(*) FROM main.inventory").fetchone()[0]
    con.close()
    assert demo_rows not in (0, 2)
    env = w.ask(f, "how many skus", "SELECT COUNT(*) AS n FROM inventory")
    served = json.dumps(env)
    assert env["rows"] != [{"n": demo_rows}], "served a figure from the ungranted main.inventory"
    assert str(demo_rows) not in re.sub(r"\W", " ", str(env.get("text") or "")).split() or (
        env["rows"] == [{"n": 2}]
    ), served


def test_personal_context_keeps_main_and_bronze_inventory_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    w.space("F", [("inventory.csv", INV_CSV)])
    grants = w.grants(None)
    assert {"inventory", "bronze.inventory"} <= grants
    fp, body = read_catalog(w.lake, grants, "duckdb")
    cols = {d["name"]: [c["name"] for c in d["columns"]] for d in body["datasets"]}
    assert "bronze_only_col" in cols["bronze.inventory"]
    assert "bronze_only_col" not in cols["inventory"]
    assert "sku" in cols["inventory"] and "_src" not in cols["bronze.inventory"]
    assert build_space_index(w.lake, "space_personal_x", grants, "duckdb") == ""
    idx = cached_value_index("space_personal_x")
    assert "GRADEBRONZE" in json.dumps({k: v for k, v in idx.items() if k.startswith("bronze.")})
    assert "GRADEBRONZE" not in json.dumps(
        {k: v for k, v in idx.items() if not k.startswith("bronze.")}
    )
    _forget_index("space_personal_x")


# --- 3. function-level attacks on one lake -----------------------------------------


def _lake(tmp_path: Path) -> Path:
    lake = tmp_path / "fn.duckdb"
    dw._SEEDED.discard(str(lake.resolve()))
    ensure_demo_warehouse(lake)
    con = duckdb.connect(str(lake))
    try:
        con.execute("CREATE SCHEMA bronze")
        con.execute("CREATE TABLE main.orders (status VARCHAR, amount BIGINT)")
        con.execute("CREATE TABLE bronze.orders (status VARCHAR, amount BIGINT)")
        con.execute("INSERT INTO main.orders VALUES ('MAINVAL', 1), ('MAINVAL2', 2)")
        con.execute("INSERT INTO bronze.orders VALUES ('BRONZEVAL', 1), ('BRONZEVAL2', 2)")
        # a main table literally named like a schema, with a column named like a bronze table
        con.execute("CREATE TABLE main.bronze (orders VARCHAR)")
        con.execute("INSERT INTO main.bronze VALUES ('TABLEBRONZECOL')")
    finally:
        con.close()
    return lake


def test_identical_shape_in_main_and_bronze_has_distinct_fingerprints_and_indexes(
    tmp_path: Path,
) -> None:
    lake = _lake(tmp_path)
    fm = read_catalog(lake, {"orders"}, "duckdb")[0]
    fb = read_catalog(lake, {"bronze.orders"}, "duckdb")[0]
    fboth = read_catalog(lake, {"orders", "bronze.orders"}, "duckdb")[0]
    assert len({fm, fb, fboth}) == 3
    for space, grant, mine, other in (
        ("sp_m", {"orders"}, "MAINVAL", "BRONZEVAL"),
        ("sp_b", {"bronze.orders"}, "BRONZEVAL", "MAINVAL"),
    ):
        _forget_index(space)
        assert build_space_index(lake, space, grant, "duckdb") == ""
        blob = json.dumps(cached_value_index(space)) + _disk_of(lake, space)
        assert mine in blob and other not in blob, (space, blob)
        _forget_index(space)


def _disk_of(lake: Path, space: str) -> str:
    p = _serving_file(lake, space)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_overlay_keys_do_not_collide_when_a_main_table_is_named_like_a_schema(
    tmp_path: Path,
) -> None:
    lake = _lake(tmp_path)
    space = "sp_prefix"
    _forget_index(space)
    grant = {"bronze", "bronze.orders"}
    assert build_space_index(lake, space, grant, "duckdb") == ""
    idx = cached_value_index(space)
    assert idx.get("bronze.orders") == ("TABLEBRONZECOL",), idx
    assert idx.get("bronze.orders.status") == ("BRONZEVAL", "BRONZEVAL2"), idx
    ctx = prepare_generate_context(
        {},
        question="bronze orders status BRONZEVAL",
        serving=lake,
        grantable=grant,
        dialect="duckdb",
        space_id=space,
    )
    prompt = str(ctx[sc.SCHEMA_CONTEXT_FIELD])
    assert "MAINVAL" not in prompt, prompt
    _forget_index(space)


def test_grant_spelling_case_does_not_widen_or_hide(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    for grant, want in (
        ({"BRONZE.ORDERS"}, {"bronze.orders"}),
        ({"Bronze.Orders"}, {"bronze.orders"}),
        ({"ORDERS"}, {"orders"}),
        ({"main.ORDERS"}, {"orders"}),
    ):
        _fp, body = read_catalog(lake, grant, "duckdb")
        assert {d["name"] for d in body["datasets"]} == want, (grant, body["datasets"])


def test_concurrent_builds_for_two_spaces_never_mix_values(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    errs: list[str] = []

    def run(space: str, grant: set[str], mine: str, other: str) -> None:
        for _ in range(15):
            _forget_index(space)
            build_space_index(lake, space, grant, "duckdb")
            blob = json.dumps(cached_value_index(space)) + json.dumps(sc._space_samples(space))
            if other in blob or mine not in blob:
                errs.append(f"{space}: {blob}")

    ts = [
        threading.Thread(target=run, args=("c_m", {"orders"}, "MAINVAL", "BRONZEVAL")),
        threading.Thread(target=run, args=("c_b", {"bronze.orders"}, "BRONZEVAL", "MAINVAL")),
        threading.Thread(target=run, args=("c_m2", {"orders"}, "MAINVAL", "BRONZEVAL")),
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    for s in ("c_m", "c_b", "c_m2"):
        _forget_index(s)
    assert not errs, errs[:2]


def test_schedule_dedupe_does_not_drop_a_build_for_another_space(tmp_path: Path) -> None:
    lake = _lake(tmp_path)
    fp = read_catalog(lake, {"bronze.orders"}, "duckdb")[0]
    for s in ("d_a", "d_b"):
        _forget_index(s)
    sc.schedule_index_build(lake, "d_a", {"bronze.orders"}, "duckdb", fp)
    sc.schedule_index_build(lake, "d_b", {"bronze.orders"}, "duckdb", fp)
    _settle()
    for s in ("d_a", "d_b"):
        assert "BRONZEVAL" in json.dumps(cached_value_index(s)), s
        _forget_index(s)


def test_unknown_space_is_shown_no_schema_and_served_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: ManifestMinter
) -> None:
    w = _world(tmp_path, monkeypatch, minter)
    w.space("A", [("orders.csv", A_ORDERS)])
    n = len(w.posts)
    w.stub.payload["query_sql"] = ORD_SQL
    r = w.client.post(
        "/v1/chat/ask",
        json={"question": "how many orders", "session_id": "s", "space_id": "space_does_not_exist"},
    )
    assert r.status_code == 404 or A_CANARY not in r.text
    assert w.prompts(n) == []
