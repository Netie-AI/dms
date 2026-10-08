"""GRANT-KEY-01: grants are (source, table) on the normal ask path.

The pipeline tests call ``Executor.live_ask``. The AI SQL is whatever
``compute_insights`` returns. A keyed grant only changes the relations and
row filters that path and the Cortex manifest see.

On main the grant is the bare table, so the same calls do not keep a source
filter. On the previous head a local planner executed unfiltered SQL.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest
from dms_executor import Executor
from dms_executor.acl import SourceGrant
from dms_executor.demo_grants import (
    DEMO_SPACE_GRANTS,
    company_default_tables,
)
from dms_executor.demo_warehouse import connect_file, ensure_demo_warehouse

from tests.fixtures.ask_guide.capture_flag_off_52 import HERE, dump_rows, replay_pack

SPACE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"
SPACE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2"
SPACE_BOTH = "cccccccc-cccc-cccc-cccc-ccccccccccc3"
SRC_A = uuid.uuid5(uuid.NAMESPACE_URL, "grant-key-src-a")
SRC_B = uuid.uuid5(uuid.NAMESPACE_URL, "grant-key-src-b")
SRC_UUID = uuid.uuid5(uuid.NAMESPACE_URL, "grant-key-src-uuid")
UUID_SOURCE = "550e8400-e29b-41d4-a716-446655440000"
QUESTION = "orders region marker"
_AS_OF = "<as_of>"
_CAPTURE_DAY = (2026, 10, 8)
GOLDEN = HERE / "flag_off_grant_key_execute.json"


def _grant(
    source_id: uuid.UUID,
    source_name: str,
    predicate: str,
) -> SourceGrant:
    kwargs: dict[str, Any] = {
        "source_id": source_id,
        "kind": "sql",
        "table_name": "orders",
        "row_predicate": predicate,
    }
    try:
        return SourceGrant(**kwargs, source_name=source_name)
    except TypeError:
        return SourceGrant(**kwargs)


class _Store:
    """Spaces and the source grants each one holds."""

    def __init__(self, spaces: dict[str, list[SourceGrant]]) -> None:
        self._spaces = spaces

    def is_space_member(self, space_id: str, user_id: str) -> bool:
        return space_id in self._spaces

    def list_space_source_ids(self, space_id: str) -> list[uuid.UUID]:
        return [g.source_id for g in self._spaces.get(space_id, [])]

    def list_user_source_grants(self, tenant_id: str, user_id: str) -> list[SourceGrant]:
        seen: list[SourceGrant] = []
        for grants in self._spaces.values():
            for grant in grants:
                if grant not in seen:
                    seen.append(grant)
        return seen

    def default_pool_id(self, tenant_id: str) -> str:
        return "default"


def _warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "keyed_orders.duckdb"
    ensure_demo_warehouse(path)
    con = connect_file(path)
    try:
        con.execute("CREATE SCHEMA src_a")
        con.execute("CREATE SCHEMA src_b")
        con.execute(f'CREATE SCHEMA "{UUID_SOURCE}"')
        con.execute(
            "CREATE TABLE src_a.orders "
            "(marker VARCHAR, region VARCHAR, marker_email VARCHAR)"
        )
        con.execute("CREATE TABLE src_b.orders (marker VARCHAR, region VARCHAR)")
        con.execute(f'CREATE TABLE "{UUID_SOURCE}".orders (marker VARCHAR, region VARCHAR)')
        con.execute("CREATE TABLE orders (marker VARCHAR, region VARCHAR)")
        con.execute(
            "INSERT INTO src_a.orders VALUES "
            "('MY-ROW', 'MY', 'person.lane.8841@example.test'), "
            "('SG-ROW', 'SG', 'person.lane.8841@example.test')"
        )
        con.execute(
            "INSERT INTO src_b.orders VALUES ('B-SG', 'SG'), ('B-MY', 'MY')"
        )
        con.execute(
            f"""INSERT INTO "{UUID_SOURCE}".orders VALUES ('UUID-ROW', 'MY')"""
        )
        # The unqualified relation a bare-name grant reads.
        con.execute("INSERT INTO orders VALUES ('MAIN-SG', 'SG')")
    finally:
        con.close()
    return path


def _apply_manifest(sql: str, predicates: dict[str, str]) -> str:
    """What Cortex does with a manifest filter: the relation is already filtered."""
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(sql, read="duckdb")
    for table in list(tree.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            continue
        schema = str(table.db or "")
        name = str(table.name or "")
        rel = f"{schema}.{name}" if schema else name
        pred = ""
        for key, val in predicates.items():
            if str(key).lower() == rel.lower():
                pred = str(val or "")
                break
        if not pred or pred.strip().upper() == "TRUE":
            continue
        rel_sql = table.sql(dialect="duckdb")
        sub = sqlglot.parse_one(
            f"SELECT * FROM {rel_sql} WHERE {pred}",
            read="duckdb",
        )
        alias = str(table.alias or name)
        table.replace(
            exp.Subquery(
                this=sub,
                alias=exp.TableAlias(this=exp.to_identifier(alias)),
            )
        )
    return tree.sql(dialect="duckdb")


class _Cortex:
    """Insights writes the SQL. Submit applies the manifest, then the ledger."""

    def __init__(self, warehouse: Path, sql: str) -> None:
        self._warehouse = warehouse
        self._sql = sql
        self.schemas: list[Any] = []
        self.insights: list[Any] = []
        self.predicates: dict[str, str] = {}
        self.submitted: list[str] = []
        self.applied: list[str] = []
        self.unfiltered: list[list[dict[str, Any]]] = []
        self.asked = False

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        onto = kwargs.get("ontology") or {}
        self.schemas.append(onto.get("schema"))
        self.insights.append(onto)
        return {"query_sql": self._sql, "plan_source": "ontology_plan"}

    def submit(self, req: Any) -> Any:
        from cortex_contract.execution import QueryResult

        plan = req.plan if isinstance(req.plan, dict) else {}
        if plan.get("kind") == "session_bind":
            return QueryResult(ok=True, status="bound", run_id="bind-1")
        sql = str((req.body or {}).get("sql") or "")
        preds = dict(getattr(req.manifest, "row_predicates", {}) or {})
        self.predicates = preds
        self.submitted.append(sql)
        bound = _apply_manifest(sql, preds)
        self.applied.append(bound)
        con = duckdb.connect(str(self._warehouse))
        try:
            def _rows(statement: str) -> list[dict[str, Any]]:
                cur = con.execute(statement)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

            self.unfiltered.append(_rows(sql))
            rows = _rows(bound)
        finally:
            con.close()
        return QueryResult(ok=True, status="ok", run_id="run-1", output={"rows": rows})

    def ledger_append(self, req: Any) -> Any:
        return SimpleNamespace(entry_id="led-grant-key", hash="hash-grant-key")

    def ask(self, req: Any) -> Any:
        from cortex_client.models import AskResponse

        self.asked = True
        return AskResponse(
            answer="MAIN-SG",
            abstained=False,
            badge="certified",
            sql_used="SELECT marker, region FROM orders",
            rows=[{"marker": "MAIN-SG", "region": "SG"}],
            audit_id="aud-ask",
            route="sql",
        )


def _minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    from cortex_contract.execution import Manifest
    from dms_executor.manifest import ManifestMinter, SessionAcl

    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="test-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-10-08T00:00:00+00:00",
            expires_at="2026-10-08T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


def _ask(
    monkeypatch: pytest.MonkeyPatch,
    warehouse: Path,
    store: _Store,
    space: str,
    sql: str,
) -> tuple[dict[str, Any], _Cortex]:
    cortex = _Cortex(warehouse, sql)
    exe = Executor(
        cortex=cortex,  # type: ignore[arg-type]
        minter=_minter(monkeypatch),
        session_store=store,
        warehouse_path=warehouse,
    )
    env = exe.live_ask(
        QUESTION,
        space_id=space,
        session_id=f"ses_{space[:8]}",
        ask_path="generative",
    )
    return env, cortex


def _markers(env: dict[str, Any]) -> list[Any]:
    return [row.get("marker") for row in env.get("rows") or []]


def test_region_filter_never_serves_the_other_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """src_a.orders granted with region = 'MY' never serves the SG row.

    The AI writes ``FROM orders``. One source resolves that to src_a. The
    manifest carries the filter. Cortex submit applies it.
    """
    warehouse = _warehouse(tmp_path)
    store = _Store(
        {
            SPACE_A: [_grant(SRC_A, "src_a", "region = 'MY'")],
            SPACE_B: [_grant(SRC_B, "src_b", "region = 'SG'")],
        }
    )
    env, cortex = _ask(
        monkeypatch,
        warehouse,
        store,
        SPACE_A,
        "SELECT marker, region FROM orders",
    )
    assert _markers(env) == ["MY-ROW"], env
    assert env["abstained"] is False
    # The AI SQL has no filter. The stub Cortex applies the manifest, and
    # that is what drops the other row.
    assert cortex.submitted
    assert "region = 'MY'" not in cortex.submitted[0]
    assert "region = 'MY'" in cortex.applied[0]
    assert {row["marker"] for row in cortex.unfiltered[0]} >= {"MY-ROW", "SG-ROW"}
    blob = json.dumps(env)
    assert "SG-ROW" not in blob
    assert "B-SG" not in blob
    assert "MAIN-SG" not in blob
    assert "src_a" in str(env.get("sql_used"))
    assert "src_b" not in str(env.get("sql_used"))
    assert cortex.predicates.get("src_a.orders") == "region = 'MY'"
    schema_blob = json.dumps(cortex.schemas)
    assert "src_a.orders" in schema_blob
    assert "src_b" not in schema_blob
    assert cortex.asked is False
    env_b, cortex_b = _ask(
        monkeypatch,
        warehouse,
        store,
        SPACE_B,
        "SELECT marker, region FROM orders",
    )
    assert _markers(env_b) == ["B-SG"], env_b
    assert "B-MY" not in json.dumps(env_b)
    assert "src_b" in str(env_b.get("sql_used"))
    assert "src_a" not in str(env_b.get("sql_used"))
    assert cortex_b.predicates.get("src_b.orders") == "region = 'SG'"


def test_wrong_source_and_main_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Space granted only src_a refuses src_b.orders and main.orders."""
    warehouse = _warehouse(tmp_path)
    store = _Store({SPACE_A: [_grant(SRC_A, "src_a", "region = 'MY'")]})
    for sql in (
        "SELECT marker, region FROM src_b.orders",
        "SELECT marker, region FROM main.orders",
    ):
        env, cortex = _ask(monkeypatch, warehouse, store, SPACE_A, sql)
        text = json.dumps(env)
        assert env["rows"] == [], env
        assert env["abstained"] is True
        assert "grant_key_missing" in text
        assert "grant_key_unqualified" not in text
        assert "SG-ROW" not in text and "MAIN-SG" not in text and "B-SG" not in text
        assert cortex.submitted == []
    from dms_executor.grant_key import (
        GrantKey,
        check_grant_sql,
        relation_allowed,
        serve_gap_grantable,
    )

    keys = frozenset({GrantKey("src_a", "orders")})
    assert serve_gap_grantable(keys) == {"src_a.orders"}
    assert relation_allowed("src_b", "orders", keys) == "grant_key_missing"
    assert relation_allowed("main", "orders", keys) == "grant_key_missing"
    assert check_grant_sql("SELECT marker FROM src_b.orders", keys) == "grant_key_missing"
    assert check_grant_sql("SELECT marker FROM main.orders", keys) == "grant_key_missing"
    from dms_executor.grant_struct import serve_gap

    grantable = serve_gap_grantable(keys)
    for sql in (
        "SELECT marker FROM src_b.orders",
        "SELECT marker FROM main.orders",
        "SELECT a.marker FROM src_a.orders a JOIN src_b.orders b ON TRUE",
        "WITH recent AS (SELECT marker FROM src_b.orders) "
        "SELECT marker FROM recent",
    ):
        assert serve_gap(sql, grantable=grantable, dialect="duckdb") == "grant_key_missing"
        assert check_grant_sql(sql, keys) == "grant_key_missing"


def test_single_source_bare_and_cte_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One source: bare FROM orders serves, and a CTE over that table serves."""
    warehouse = _warehouse(tmp_path)
    one = _Store({SPACE_A: [_grant(SRC_A, "src_a", "region = 'MY'")]})
    env, _cortex = _ask(
        monkeypatch,
        warehouse,
        one,
        SPACE_A,
        "SELECT marker, region FROM orders",
    )
    assert _markers(env) == ["MY-ROW"], env
    cte, _cortex = _ask(
        monkeypatch,
        warehouse,
        one,
        SPACE_A,
        "WITH recent AS (SELECT marker, region FROM orders) "
        "SELECT marker, region FROM recent",
    )
    assert _markers(cte) == ["MY-ROW"], cte
    assert "recent" in str(cte.get("sql_used"))
    assert "src_a" in str(cte.get("sql_used"))
    from dms_executor.grant_key import GrantKey, check_grant_sql, relation_allowed

    one_key = frozenset({GrantKey("src_a", "orders")})
    assert relation_allowed(None, "orders", one_key) == "ok"
    assert check_grant_sql(
        "WITH recent AS (SELECT marker FROM orders) SELECT marker FROM recent",
        one_key,
    ) == "ok"


def test_join_and_cte_refuse_the_other_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """src_b.orders is refused as a JOIN and inside a CTE. Nothing is submitted."""
    warehouse = _warehouse(tmp_path)
    store = _Store({SPACE_A: [_grant(SRC_A, "src_a", "region = 'MY'")]})
    shapes = (
        "SELECT a.marker, a.region FROM src_a.orders a "
        "JOIN src_b.orders b ON TRUE",
        "WITH recent AS (SELECT marker, region FROM src_b.orders) "
        "SELECT marker, region FROM recent",
    )
    for sql in shapes:
        env, cortex = _ask(monkeypatch, warehouse, store, SPACE_A, sql)
        text = json.dumps(env)
        assert env["rows"] == [], env
        assert env["abstained"] is True
        assert "grant_key_missing" in text
        assert cortex.submitted == []
        assert "B-SG" not in text and "SG-ROW" not in text


_PERSON = "person.lane.8841@example.test"


def test_keyed_samples_stay_out_of_insights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person-like value and a live region value never reach Insights.

    The catalog and the body are the ontology passed to compute_insights.
    Mask failure publishes no samples and the served ask still runs.
    """
    warehouse = _warehouse(tmp_path)
    store = _Store({SPACE_A: [_grant(SRC_A, "src_a", "region = 'MY'")]})
    env, cortex = _ask(
        monkeypatch,
        warehouse,
        store,
        SPACE_A,
        "SELECT marker, region FROM orders",
    )
    assert _markers(env) == ["MY-ROW"], env
    body = json.dumps(cortex.insights)
    assert _PERSON not in body
    assert cortex.insights
    encodings = json.dumps(
        [item.get("encodings") for item in cortex.insights if isinstance(item, dict)]
    )
    catalog = json.dumps(
        [item.get("schema") for item in cortex.insights if isinstance(item, dict)]
    )
    assert "MY" not in encodings and "SG" not in encodings
    assert _PERSON not in catalog and "MY" not in catalog

    def _mask_down(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mask down")

    monkeypatch.setattr(
        "dms_executor.semantic_retrieve.fail_closed_mask_payload",
        _mask_down,
    )
    failed, failed_cortex = _ask(
        monkeypatch,
        warehouse,
        store,
        SPACE_A,
        "SELECT marker, region FROM orders",
    )
    assert _markers(failed) == ["MY-ROW"], failed
    failed_body = json.dumps(failed_cortex.insights)
    assert _PERSON not in failed_body
    failed_encodings = [
        item.get("encodings")
        for item in failed_cortex.insights
        if isinstance(item, dict)
    ]
    assert failed_encodings
    assert all(item == {} for item in failed_encodings)


def test_plain_mask_failure_sends_no_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare-name retrieve also publishes nothing when the mask fails."""
    from dms_executor.semantic_retrieve import (
        retrieve_short_context,
        retrieve_value_encodings,
    )

    warehouse = tmp_path / "plain.duckdb"
    ensure_demo_warehouse(warehouse)
    schema = [{"table": "inventory", "columns": ["category"], "score": 5}]
    toks = {"inventory", "category"}
    present = retrieve_value_encodings(warehouse, schema, toks)
    assert present, present

    def _mask_down(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("mask down")

    monkeypatch.setattr(
        "dms_executor.semantic_retrieve.fail_closed_mask_payload",
        _mask_down,
    )
    assert retrieve_value_encodings(warehouse, schema, toks) == {}
    ctx = retrieve_short_context(
        "inventory category",
        warehouse=warehouse,
        grantable={"inventory"},
    )
    assert ctx.get("encodings") == {}


def test_checker_and_pipeline_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """serve_gap and the served ask agree on each case.

    The rows are the replay table: one source bare, the CTE over it, the
    collision, and the qualified misses.
    """
    from dms_executor.grant_key import GrantKey, serve_gap_grantable
    from dms_executor.grant_struct import serve_gap

    warehouse = _warehouse(tmp_path)
    one = _Store({SPACE_A: [_grant(SRC_A, "src_a", "region = 'MY'")]})
    both = _Store(
        {
            SPACE_BOTH: [
                _grant(SRC_A, "src_a", "TRUE"),
                _grant(SRC_B, "src_b", "TRUE"),
            ]
        }
    )
    one_keys = frozenset({GrantKey("src_a", "orders")})
    two_keys = frozenset({GrantKey("src_a", "orders"), GrantKey("src_b", "orders")})
    bare = "SELECT marker, region FROM orders"
    cte = (
        "WITH recent AS (SELECT marker, region FROM orders) "
        "SELECT marker, region FROM recent"
    )
    wrong = "SELECT marker, region FROM src_b.orders"
    joined = (
        "SELECT a.marker, a.region FROM src_a.orders a "
        "JOIN src_b.orders b ON TRUE"
    )
    cte_other = (
        "WITH recent AS (SELECT marker, region FROM src_b.orders) "
        "SELECT marker, region FROM recent"
    )
    cases = (
        ("bare_one_source", one, SPACE_A, one_keys, bare, None, ["MY-ROW"]),
        ("cte_one_source", one, SPACE_A, one_keys, cte, None, ["MY-ROW"]),
        ("collision", both, SPACE_BOTH, two_keys, bare, "grant_key_ambiguous", []),
        ("wrong_source", one, SPACE_A, one_keys, wrong, "grant_key_missing", []),
        ("join_other", one, SPACE_A, one_keys, joined, "grant_key_missing", []),
        ("cte_other", one, SPACE_A, one_keys, cte_other, "grant_key_missing", []),
    )
    table: list[tuple[str, str | None, list[Any]]] = []
    for name, store, space, keys, sql, code, markers in cases:
        gap = serve_gap(sql, grantable=serve_gap_grantable(keys), dialect="duckdb")
        env, cortex = _ask(monkeypatch, warehouse, store, space, sql)
        table.append((name, gap, _markers(env)))
        assert gap == code, table
        assert _markers(env) == markers, table
        if code:
            assert env["abstained"] is True
            assert env["badge"] == "ABSTAIN"
            assert code in json.dumps(env)
            assert cortex.submitted == []
        else:
            assert env["abstained"] is False
            assert cortex.submitted
    assert [row[0] for row in table] == [row[0] for row in cases]


def test_two_source_bare_name_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sources that both grant orders: bare FROM orders is a named refusal."""
    warehouse = _warehouse(tmp_path)
    both = _Store(
        {
            SPACE_BOTH: [
                _grant(SRC_A, "src_a", "TRUE"),
                _grant(SRC_B, "src_b", "TRUE"),
            ]
        }
    )
    collided, cortex = _ask(
        monkeypatch,
        warehouse,
        both,
        SPACE_BOTH,
        "SELECT marker, region FROM orders",
    )
    text = json.dumps(collided)
    assert collided["rows"] == [], collided
    assert collided["badge"] == "ABSTAIN"
    assert "grant_key_ambiguous" in text
    assert "UNION" not in str(collided.get("sql_used"))
    assert cortex.submitted == []
    assert "MY-ROW" not in text and "B-SG" not in text
    from dms_executor.grant_key import GrantKey, relation_allowed

    two = frozenset({GrantKey("src_a", "orders"), GrantKey("src_b", "orders")})
    assert relation_allowed(None, "orders", two) == "grant_key_ambiguous"


def test_hyphenated_source_id_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse = _warehouse(tmp_path)
    store = _Store({SPACE_A: [_grant(SRC_UUID, UUID_SOURCE, "TRUE")]})
    env, cortex = _ask(
        monkeypatch,
        warehouse,
        store,
        SPACE_A,
        f'SELECT marker, region FROM "{UUID_SOURCE}".orders',
    )
    assert _markers(env) == ["UUID-ROW"], env
    assert UUID_SOURCE in str(env.get("sql_used"))
    assert f"{UUID_SOURCE}.orders" in cortex.predicates
    schema_blob = json.dumps(cortex.schemas)
    assert UUID_SOURCE in schema_blob


def test_migration_keeps_each_space_readable_set(tmp_path: Path) -> None:
    before = {
        space_id: frozenset(tables)
        for space_id, (_name, tables) in DEMO_SPACE_GRANTS.items()
    }
    try:
        from dms_executor.grant_key import migrate_seed, readable_tables
    except ImportError:
        after = before
    else:
        migrated = migrate_seed(DEMO_SPACE_GRANTS)
        after = {space_id: readable_tables(keys) for space_id, keys in migrated.items()}
        assert set(migrated) == set(before)
        for space_id, keys in migrated.items():
            assert keys
            for key in keys:
                assert key.source != key.table
                assert "." not in key.table
            # Same table name in two Spaces is two keys (the source id differs
            # per table, and a shared table keeps the source it already had).
            assert readable_tables(keys) == before[space_id]
    assert after == before
    assert frozenset(company_default_tables()) == frozenset().union(*before.values())
    exe = Executor(cortex=None, warehouse_path=tmp_path / "migrated.duckdb")
    grant_keys = getattr(exe._session_store, "grant_keys", None)
    for space_id, tables in before.items():
        assert frozenset(exe.grantable_tables(space_id=space_id)) == tables
        if grant_keys is not None:
            assert {key.table for key in grant_keys(space_id)} == tables


def _pin_capture_day(monkeypatch: pytest.MonkeyPatch) -> None:
    base = dt.date

    class _CaptureDate(base):
        @classmethod
        def today(cls) -> dt.date:
            return base(*_CAPTURE_DAY)

    monkeypatch.setattr(dt, "date", _CaptureDate)


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    out = dict(env)
    if "as_of" in out:
        out["as_of"] = _AS_OF
    return out


def _diff_paths(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list):
        if len(left) != len(right):
            return [path or "$"]
        paths = []
        for index, (item, other) in enumerate(zip(left, right, strict=True)):
            paths.extend(_diff_paths(item, other, f"{path}[{index}]"))
        return paths
    if left != right:
        return [path or "$"]
    return []


def test_golden_envelopes_match_main_including_served_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full envelope, including served_check_shadow and served_*.

    The stub executes submitted SQL. ``as_of`` is the wall clock.
    """
    _connect = duckdb.connect

    def _connect_one(*args: Any, **kwargs: Any) -> Any:
        con = _connect(*args, **kwargs)
        try:
            con.execute("PRAGMA threads=1")
        except Exception:
            pass
        return con

    monkeypatch.setattr(duckdb, "connect", _connect_one)
    _pin_capture_day(monkeypatch)
    live = replay_pack(execute=True)
    assert len(live) == 52
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert [row["id"] for row in live] == [row["id"] for row in golden]
    left = dump_rows([{"id": row["id"], "env": _stable(row["env"])} for row in live])
    right = dump_rows(
        [{"id": row["id"], "env": _stable(row["env"])} for row in golden]
    )
    if left != right:
        main_rows = {row["id"]: row["env"] for row in golden}
        problems: list[str] = []
        for row in live:
            paths = _diff_paths(_stable(row["env"]), _stable(main_rows[row["id"]]))
            if paths:
                problems.append(f"{row['id']}: {paths}")
        pytest.fail("envelopes differ from main:\n" + "\n".join(problems))
    assert any("served_check_shadow" in row["env"] for row in live)
    assert all("served_attribution" in row["env"] for row in live)
