"""Allow-list grant gate. These tests fail on 95f54f06.

Every entry point refuses with a named reason and does not submit.
A granted quoted relation, a trailing semicolon, and a semicolon inside a
string or a comment still serve.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_contract.execution import Manifest, QueryResult
from dms_executor import DEMO_TABLES, Executor
from dms_executor.demo_pack import PackMetric, maybe_pack_ask
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.grant_struct import normalize_relation, serve_gap, structural_grant_stop
from dms_executor.manifest import SecurityEvent, SessionAcl
from dms_executor.session_followup import run_followup_sql
from dms_executor.verified_queries import (
    _TABLE,
    lookup_verified_query,
    maybe_verified_ask,
    register_verified_query,
)

OPS = "dddddddd-dddd-dddd-dddd-dddddddddddd"
Q = "How many rows are there?"

# reason is a prefix. Hostile file functions keep the scanner sentence.
REFUSE = (
    ("passwd", "SELECT * FROM '/etc/passwd'", "sql_relation_not_granted", "/etc/passwd"),
    (
        "read_csv_auto",
        "SELECT * FROM read_csv_auto('/etc/passwd')",
        "hostile_sql:",
        "/etc/passwd",
    ),
    (
        "read_text",
        "SELECT * FROM read_text('/etc/hostname')",
        "sql_relation_not_granted",
        "/etc/hostname",
    ),
    (
        "read_parquet",
        "SELECT * FROM read_parquet('*.parquet')",
        "hostile_sql:",
        "*.parquet",
    ),
    (
        "glob",
        "SELECT * FROM glob('/var/cortex/*')",
        "sql_relation_not_granted",
        "/var/cortex",
    ),
    (
        "iceberg",
        "SELECT * FROM iceberg_scan(beta_secret)",
        "sql_relation_not_granted",
        "beta_secret",
    ),
    (
        "cte",
        "WITH c AS (SELECT * FROM read_text('/etc/hostname')) SELECT * FROM c",
        "sql_relation_not_granted",
        "/etc/hostname",
    ),
    (
        "join",
        "SELECT * FROM inventory JOIN iceberg_scan(beta_secret) t ON TRUE",
        "sql_relation_not_granted",
        "beta_secret",
    ),
    (
        "subquery",
        "SELECT * FROM (SELECT * FROM glob('/var/cortex/*')) s",
        "sql_relation_not_granted",
        "/var/cortex",
    ),
    (
        "information_schema",
        "SELECT * FROM information_schema.tables",
        "sql_relation_not_granted",
        "information_schema",
    ),
    ("checkpoint", "SELECT 1; CHECKPOINT", "multi_statement", ""),
    ("attach", "SELECT 1;ATTACH ':memory:' AS x", "multi_statement", ""),
    ("copy", "sElEcT 1; cOpY (SELECT 1) TO '/tmp/x.csv'", "multi_statement", "/tmp/x.csv"),
    ("two_select", "SELECT 1; SELECT 2", "multi_statement", ""),
    ("block_comment", "SELECT 1\n/* ; */\nSELECT 2", "ungranted:unparsed", ""),
    ("line_comment", "SELECT 1\n-- note\nSELECT 2", "ungranted:unparsed", ""),
)

SERVE = (
    'SELECT * FROM "orders"',
    'SELECT * FROM "orders";',
    "SELECT * FROM \"orders\" WHERE note = 'a;b'",
    'SELECT * FROM "orders" /* a;b */',
    "SELECT * FROM orders",
    "SELECT * FROM main.orders",
    'SELECT * FROM "Orders"',
    "WITH c AS (WITH d AS (SELECT * FROM orders) SELECT * FROM d) SELECT * FROM c",
    "SELECT * FROM (SELECT note FROM orders) s",
    "SELECT o.note FROM orders o",
    "SELECT a.note FROM orders a JOIN orders b ON a.note = b.note",
    "SELECT * FROM orders WHERE EXISTS (SELECT 1 FROM orders o2 WHERE o2.note = orders.note)",
    "SELECT note FROM orders UNION SELECT note FROM orders",
)


class _Gen:
    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.submits: list[Any] = []

    def compute_insights(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return {"query_sql": self.sql, "plan_source": "ontology_plan"}

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        return QueryResult(ok=True, status="ok", run_id="run_g2", output={"rows": [{"n": 1}]})

    def ledger_append(self, req: Any) -> Any:
        from cortex_client.models import LedgerAppendResponse

        return LedgerAppendResponse(entry_id="led_g2", hash="hash_g2_not_led")

    def ask(self, req: Any) -> Any:
        from cortex_client.models import AskResponse

        return AskResponse(answer="fallback", abstained=True, badge="abstain", rows=[])


class _Fallback:
    """Generative miss, then Cortex.ask returns this SQL."""

    def __init__(self, sql: str) -> None:
        self.sql = sql
        self.submits: list[Any] = []
        self.asks = 0

    def compute_insights(self, question: str, **kwargs: Any) -> None:
        return None

    def submit(self, req: Any) -> QueryResult:
        self.submits.append(req)
        return QueryResult(ok=True, status="ok", run_id="run_fb", output={"rows": []})

    def ask(self, req: Any) -> Any:
        from cortex_client.models import AskResponse

        self.asks += 1
        return AskResponse(
            answer="1",
            abstained=False,
            badge="generated",
            rows=[{"n": 1}],
            sql_used=self.sql,
            route="generated",
        )


def _minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    from dms_executor.manifest import ManifestMinter

    minter = ManifestMinter()

    def _mint(acl: SessionAcl) -> Manifest:
        return Manifest(
            session_id=acl.session_id,
            org_id=acl.org_id,
            space_id=acl.space_id,
            pool_id=acl.pool_id,
            issuer_key_id="grant-struct-kid",
            allowed_paths=list(acl.allowed_paths),
            row_predicates=dict(acl.row_predicates),
            issued_at="2026-07-30T00:00:00+00:00",
            expires_at="2026-07-30T01:00:00+00:00",
            signature="dGVzdHNpZw",
        )

    monkeypatch.setattr(minter, "mint_manifest", _mint)
    monkeypatch.setattr(minter, "fetch_intermediate", lambda: None)
    monkeypatch.setattr(minter, "close", lambda: None)
    monkeypatch.setattr(minter, "invalidate", lambda *_a, **_k: None)
    return minter


def _reason(env: dict[str, Any]) -> str:
    return " ".join(str(item) for item in (env.get("assumptions") or []))


def _acl() -> SessionAcl:
    return SessionAcl(
        session_id="ses_g2",
        org_id="org_g2",
        space_id=OPS,
        row_predicates={"orders": "TRUE", "inventory": "TRUE"},
        allowed_paths=[],
        pool_id="default",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sql_submits(rows: list[Any]) -> list[Any]:
    """Cortex SQL submits. Session bind is not the statement."""
    out = []
    for req in rows:
        plan = getattr(req, "plan", None)
        kind = plan.get("kind") if isinstance(plan, dict) else None
        if kind == "sql":
            out.append(req)
    return out


@pytest.mark.parametrize(("name", "sql", "reason", "hidden"), REFUSE)
def test_ungranted_relation_refuses_every_entry(
    name: str,
    sql: str,
    reason: str,
    hidden: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = ensure_demo_warehouse(tmp_path / "gate.duckdb")
    model = _Gen(sql)
    exe = Executor(cortex=model, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]

    env = exe.live_ask(Q, space_id=OPS, session_id="ses_g2_live")
    assert_envelope_valid(env)
    assert env["abstained"] is True, name
    assert env["rows"] == [], name
    assert env["sql_used"] is None, name
    assert reason in _reason(env), name
    assert _sql_submits(model.submits) == [], name
    if hidden:
        assert hidden not in json.dumps(env), name

    digest = _sha(db)
    user = exe.answer_user_sql(sql, space_id=OPS, session_id="ses_g2_user")
    assert_envelope_valid(user)
    assert user["abstained"] is True, name
    assert user["rows"] == [], name
    assert user["sql_used"] is None, name
    assert reason in _reason(user), name
    assert "hard rule 12" not in _reason(user), name
    if hidden:
        assert hidden not in json.dumps(user), name
    assert _sha(db) == digest, name

    before = len(_sql_submits(model.submits))
    with pytest.raises(SecurityEvent) as ei:
        exe.submit_sql(sql, _acl())
    assert reason in ei.value.code, name
    assert len(_sql_submits(model.submits)) == before, name

    import dms_executor.demo_pack as pack_mod

    real_match = pack_mod.match_pack_phrase
    pack_mod.match_pack_phrase = lambda question, tables=None: PackMetric(  # type: ignore[method-assign]
        "g2", question, sql, ()
    )
    pack_submits: list[str] = []
    try:
        pack_env = maybe_pack_ask(
            Q,
            space_id=OPS,
            session_id="ses_g2_pack",
            grantable={"orders", "inventory"},
            submit=lambda s: pack_submits.append(s),
            ledger_append=lambda _p: None,
            dialect="duckdb",
        )
    finally:
        pack_mod.match_pack_phrase = real_match
    assert pack_submits == [], name
    if pack_env is not None:
        assert_envelope_valid(pack_env)
        assert pack_env["abstained"] is True, name
        assert reason in _reason(pack_env), name
        if hidden:
            assert hidden not in json.dumps(pack_env), name

    with pytest.raises(ValueError) as ve:
        register_verified_query(
            space_id=OPS,
            question=f"vq refuse {name}",
            sql=sql,
            path=db,
            dialect="duckdb",
        )
    assert reason in str(ve.value), name
    held = register_verified_query(
        space_id=OPS,
        question=f"vq hold {name}",
        sql="SELECT 1 AS n",
        path=db,
        dialect="duckdb",
    )
    con = duckdb.connect(str(db))
    try:
        con.execute(
            f"UPDATE {_TABLE} SET sql_text = ? WHERE asset_id = ?",
            [sql, held["asset_id"]],
        )
    finally:
        con.close()
    assert (
        lookup_verified_query(
            f"vq hold {name}",
            space_id=OPS,
            warehouse=db,
            grantable={"orders", "inventory"},
            dialect="duckdb",
        )
        is None
    ), name
    vq_submits: list[str] = []
    assert (
        maybe_verified_ask(
            f"vq hold {name}",
            space_id=OPS,
            warehouse=db,
            grantable={"orders", "inventory"},
            submit=lambda s: vq_submits.append(s),
            ledger_append=lambda _p: None,
            dialect="duckdb",
        )
        is None
    ), name
    assert vq_submits == [], name

    followed = run_followup_sql(
        sql,
        warehouse=db,
        space_id=OPS,
        session_id="ses_g2_fu",
        question="average of them",
        why="gate",
        text="no",
        dialect="duckdb",
    )
    assert_envelope_valid(followed)
    assert followed["abstained"] is True, name
    assert followed["rows"] == [], name
    assert followed["sql_used"] is None, name
    assert reason in _reason(followed), name

    fb = _Fallback(sql)
    fb_exe = Executor(cortex=fb, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    fb_env = fb_exe.live_ask(Q, space_id=OPS, session_id="ses_g2_fb")
    assert_envelope_valid(fb_env)
    assert fb.asks == 1, name
    assert fb_env["abstained"] is True, name
    assert fb_env["rows"] == [], name
    assert fb_env["sql_used"] is None, name
    assert reason in _reason(fb_env), name
    assert _sql_submits(fb.submits) == [], name
    if hidden:
        assert hidden not in json.dumps(fb_env), name


def _grant_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor as pkg

    monkeypatch.setattr(pkg, "DEMO_TABLES", (*DEMO_TABLES, "orders"))
    real = pkg.Executor.grantable_tables

    def _grants(self: Executor, *, space_id: str | None) -> list[str]:
        base = list(real(self, space_id=space_id))
        if "orders" not in base:
            base.append("orders")
        return base

    monkeypatch.setattr(pkg.Executor, "grantable_tables", _grants)


@pytest.mark.parametrize("sql", SERVE)
def test_granted_quoted_and_semicolon_shapes_serve(
    sql: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "orders.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE orders (note VARCHAR)")
        con.execute("INSERT INTO orders VALUES ('a;b')")
    finally:
        con.close()
    _grant_orders(monkeypatch)
    model = _Gen(sql)
    exe = Executor(cortex=model, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(Q, space_id=OPS, session_id="ses_g2_ok")
    assert_envelope_valid(env)
    assert env["abstained"] is False, sql
    assert env["rows"], sql
    assert model.submits, sql
    user = exe.answer_user_sql(sql, space_id=OPS, session_id="ses_g2_ok_user")
    assert_envelope_valid(user)
    assert user["abstained"] is False, sql
    assert user["rows"], sql
    assert user["sql_used"], sql


# These three are served by the Cortex.ask fallback on a0e796c09fd8.
# A plain ungranted name is ``ungranted:<bare>``. A non-default schema is
# ``sql_relation_not_granted``. The pack lane keeps its grants-fail sentence
# for a plain ungranted name and does not submit.
_FALLBACK_HOLES = (
    (
        "information_schema",
        "SELECT * FROM information_schema.tables",
        "sql_relation_not_granted",
    ),
    (
        "main_secret",
        "SELECT * FROM main.secret_ungranted",
        "ungranted:secret_ungranted",
    ),
    (
        "cte_orders",
        "WITH orders AS (SELECT * FROM alerts) SELECT * FROM orders",
        "ungranted:alerts",
    ),
    (
        "cte_secret",
        "WITH orders AS (SELECT * FROM secret_ungranted) SELECT * FROM orders",
        "ungranted:secret_ungranted",
    ),
)


@pytest.mark.parametrize(("name", "sql", "reason"), _FALLBACK_HOLES)
def test_fallback_refuses_every_serve_gap(
    name: str,
    sql: str,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = ensure_demo_warehouse(tmp_path / "hole.duckdb")
    _grant_orders(monkeypatch)
    model = _Gen(sql)
    exe = Executor(cortex=model, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(Q, space_id=OPS, session_id="ses_hole_live")
    assert env["abstained"] is True, name
    assert env["sql_used"] is None, name
    assert reason in _reason(env), name
    assert _sql_submits(model.submits) == [], name

    user = exe.answer_user_sql(sql, space_id=OPS, session_id="ses_hole_user")
    assert user["abstained"] is True, name
    assert user["sql_used"] is None, name
    assert reason in _reason(user), name

    before = len(_sql_submits(model.submits))
    with pytest.raises(SecurityEvent) as ei:
        exe.submit_sql(sql, _acl())
    assert reason in ei.value.code, name
    assert len(_sql_submits(model.submits)) == before, name

    import dms_executor.demo_pack as pack_mod

    real_match = pack_mod.match_pack_phrase
    pack_mod.match_pack_phrase = lambda question, tables=None: PackMetric(  # type: ignore[method-assign]
        "hole", question, sql, ()
    )
    pack_submits: list[str] = []
    try:
        pack_env = maybe_pack_ask(
            Q,
            space_id=OPS,
            session_id="ses_hole_pack",
            grantable={"orders", "inventory"},
            submit=lambda s: pack_submits.append(s),
            ledger_append=lambda _p: None,
            dialect="duckdb",
        )
    finally:
        pack_mod.match_pack_phrase = real_match
    assert pack_submits == [], name
    assert pack_env is not None, name
    assert pack_env["abstained"] is True, name
    assert pack_env["sql_used"] is None, name
    if reason.startswith("ungranted:"):
        assert "grants fail" in _reason(pack_env), name
    else:
        assert reason in _reason(pack_env), name

    with pytest.raises(ValueError) as ve:
        register_verified_query(
            space_id=OPS, question=f"vq hole {name}", sql=sql, path=db,
            dialect="duckdb",
        )
    # A demo table outside the Space keeps the sql_not_in_space sentence.
    assert reason in str(ve.value) or str(ve.value).startswith("sql_not_in_space:"), name

    followed = run_followup_sql(
        sql,
        warehouse=db,
        space_id=OPS,
        session_id="ses_hole_fu",
        question="average of them",
        why="gate",
        text="no",
        grantable={"orders", "inventory"},
        dialect="duckdb",
    )
    assert followed["abstained"] is True, name
    assert followed["sql_used"] is None, name
    assert reason in _reason(followed), name

    fb = _Fallback(sql)
    fb_exe = Executor(cortex=fb, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    fb_env = fb_exe.live_ask(Q, space_id=OPS, session_id="ses_hole_fb")
    assert fb.asks == 1, name
    assert fb_env["abstained"] is True, name
    assert fb_env["rows"] == [], name
    assert fb_env["sql_used"] is None, name
    from dms_executor.grant_struct import customer_grant_reason

    shown = customer_grant_reason(reason)
    assert shown in _reason(fb_env), name
    fb_blob = json.dumps(fb_env)
    if shown == "ungranted":
        for part in reason.split(":", 1)[1].split(","):
            assert part not in fb_blob, name
    assert _sql_submits(fb.submits) == [], name


_SCALAR_FNS = ("read_text", "read_csv_auto", "read_parquet", "glob")
_SCALAR_SLOTS = (
    "SELECT {fn}('/tmp/x') AS v",
    "SELECT 1 AS n FROM orders WHERE {fn}('/tmp/x') IS NOT NULL",
    "SELECT * FROM (SELECT {fn}('/tmp/x') AS v) s",
)


@pytest.mark.parametrize("fn", _SCALAR_FNS)
@pytest.mark.parametrize("template", _SCALAR_SLOTS)
def test_scalar_reader_is_ungranted_in_any_slot(
    fn: str, template: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allow-list refuses a reader even when the hostile scanner is off."""
    monkeypatch.setattr(
        "dms_executor.grant_struct.reject_hostile_chat_sql",
        lambda _sql: None,
    )
    sql = template.format(fn=fn)
    assert serve_gap(sql, grantable={"orders"}, dialect="duckdb") == (
        "sql_relation_not_granted"
    )


@pytest.mark.parametrize("fn", _SCALAR_FNS)
@pytest.mark.parametrize("template", _SCALAR_SLOTS)
def test_scalar_reader_abstains_before_execute(
    fn: str,
    template: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = template.format(fn=fn)
    db = tmp_path / "scalar.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE orders (note VARCHAR)")
        con.execute("INSERT INTO orders VALUES ('a')")
    finally:
        con.close()
    _grant_orders(monkeypatch)
    exe = Executor(cortex=_Gen(sql), minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    user = exe.answer_user_sql(sql, space_id=OPS, session_id="ses_scalar")
    assert_envelope_valid(user)
    assert user["abstained"] is True, sql
    assert user["sql_used"] is None, sql
    assert user["rows"] == [], sql
    assert "sql_relation_not_granted" in _reason(user) or "hostile_sql:" in _reason(user)
    assert "/tmp/x" not in json.dumps(user)


_OTHER_SCHEMA = (
    "SELECT * FROM other_schema.orders",
    'SELECT * FROM "other_schema"."orders"',
    "WITH c AS (SELECT * FROM other_schema.orders) SELECT * FROM c",
)


@pytest.mark.parametrize("sql", _OTHER_SCHEMA)
def test_other_schema_refuses_before_explain(
    sql: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "schema.duckdb"
    ensure_demo_warehouse(db)
    con = duckdb.connect(str(db))
    try:
        con.execute("CREATE TABLE orders (note VARCHAR)")
        con.execute("INSERT INTO orders VALUES ('a')")
    finally:
        con.close()
    _grant_orders(monkeypatch)
    explained: list[str] = []
    real = duckdb.DuckDBPyConnection.execute

    def _wrapped(self: Any, statement: str, *args: Any, **kwargs: Any) -> Any:
        if str(statement).lstrip().upper().startswith("EXPLAIN"):
            explained.append(str(statement))
        return real(self, statement, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", _wrapped)
    assert serve_gap(sql, grantable={"orders"}, dialect="duckdb") == (
        "sql_relation_not_granted"
    )
    model = _Gen(sql)
    exe = Executor(cortex=model, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(Q, space_id=OPS, session_id="ses_schema")
    assert env["abstained"] is True, sql
    assert env["sql_used"] is None, sql
    assert "sql_relation_not_granted" in _reason(env), sql
    assert "CatalogException" not in _reason(env), sql
    assert _sql_submits(model.submits) == [], sql
    assert explained == [], sql
    fb = _Fallback(sql)
    fb_exe = Executor(cortex=fb, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    fb_env = fb_exe.live_ask(Q, space_id=OPS, session_id="ses_schema_fb")
    assert fb_env["abstained"] is True, sql
    assert "sql_relation_not_granted" in _reason(fb_env), sql
    assert _sql_submits(fb.submits) == [], sql
    assert explained == [], sql


def test_followup_serves_a_granted_table(tmp_path: Path) -> None:
    db = ensure_demo_warehouse(tmp_path / "follow_ok.duckdb")
    env = run_followup_sql(
        "SELECT COUNT(*) AS n FROM inventory",
        warehouse=db,
        space_id=OPS,
        session_id="ses_fu_ok",
        question="how many",
        why="granted follow-up",
        text="served",
        grantable={"inventory"},
        dialect="duckdb",
    )
    assert_envelope_valid(env)
    assert env["abstained"] is False
    assert env["rows"]
    assert env["sql_used"]


def _grant_names(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]) -> None:
    import dms_executor as pkg

    monkeypatch.setattr(pkg, "DEMO_TABLES", (*DEMO_TABLES, *names))
    real = pkg.Executor.grantable_tables

    def _grants(self: Executor, *, space_id: str | None) -> list[str]:
        base = list(real(self, space_id=space_id))
        for name in names:
            if name not in base:
                base.append(name)
        return base

    monkeypatch.setattr(pkg.Executor, "grantable_tables", _grants)


_QUAL_BAD = (
    "src_b.orders",
    "other_schema.orders",
    "catalog.src_b.orders",
    '"src_b".orders',
    "SRC_B.Orders",
)
_QUAL_POS = ("from", "join", "cte", "subquery")
_QUAL_GRANTS = (
    ("bare", {"orders"}, "orders"),
    ("qualified", {"src_a.orders"}, "src_a.orders"),
)


def _placed(position: str, relation: str, anchor: str) -> str:
    if position == "from":
        return f"SELECT 1 AS n FROM {relation}"
    if position == "join":
        return f"SELECT 1 AS n FROM {anchor} a JOIN {relation} b ON TRUE"
    if position == "cte":
        return f"WITH c AS (SELECT 1 AS n FROM {relation}) SELECT * FROM c"
    return f"SELECT * FROM (SELECT 1 AS n FROM {relation}) s"


@pytest.mark.parametrize("relation", _QUAL_BAD)
@pytest.mark.parametrize("position", _QUAL_POS)
@pytest.mark.parametrize(("grant_name", "grant", "anchor"), _QUAL_GRANTS)
def test_qualified_mismatch_refuses_before_submit(
    relation: str,
    position: str,
    grant_name: str,
    grant: set[str],
    anchor: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _placed(position, relation, anchor)
    assert serve_gap(sql, grantable=set(grant), dialect="duckdb") == (
        "sql_relation_not_granted"
    ), (grant_name, position, relation)
    _grant_names(monkeypatch, tuple(grant))
    db = ensure_demo_warehouse(tmp_path / "qual.duckdb")
    model = _Gen(sql)
    exe = Executor(cortex=model, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    env = exe.live_ask(Q, space_id=OPS, session_id="ses_qual")
    assert env["abstained"] is True
    assert env["sql_used"] is None
    assert "sql_relation_not_granted" in _reason(env)
    assert _sql_submits(model.submits) == []
    fb = _Fallback(sql)
    fb_exe = Executor(cortex=fb, minter=_minter(monkeypatch), warehouse_path=db)  # type: ignore[arg-type]
    fb_env = fb_exe.live_ask(Q, space_id=OPS, session_id="ses_qual_fb")
    assert fb_env["abstained"] is True
    assert "sql_relation_not_granted" in _reason(fb_env)
    assert _sql_submits(fb.submits) == []


def test_normalize_relation_follows_the_engine() -> None:
    """Same key the dialect folds. Quoted case differs only when the engine keeps it."""
    assert normalize_relation("orders", dialect="duckdb") == normalize_relation(
        '"Orders"', dialect="duckdb"
    )
    assert normalize_relation("orders", dialect="duckdb") == normalize_relation(
        "main.orders", dialect="duckdb"
    )
    assert normalize_relation('"SRC_A".orders', dialect="duckdb") == normalize_relation(
        "src_a.orders", dialect="duckdb"
    )
    assert normalize_relation("SRC_A.ORDERS", dialect="postgres") == normalize_relation(
        "src_a.orders", dialect="postgres"
    )
    assert normalize_relation('"SRC_A".orders', dialect="postgres") != normalize_relation(
        "src_a.orders", dialect="postgres"
    )
    assert normalize_relation("src_a.orders", dialect="snowflake") == normalize_relation(
        "SRC_A.ORDERS", dialect="snowflake"
    )
    assert normalize_relation('"src_a"."orders"', dialect="snowflake") != normalize_relation(
        "src_a.orders", dialect="snowflake"
    )
    assert normalize_relation("orders", dialect="no-such-engine") is None
    assert serve_gap("SELECT 1 FROM orders", grantable={"orders"}, dialect="nope") == (
        "sql_dialect_unknown"
    )
    assert serve_gap("SELECT * FROM ?", grantable={"orders"}, dialect="duckdb") == (
        "sql_relation_unresolved"
    )


def test_unknown_dialect_and_unsettled_name_abstain_without_submit() -> None:
    """#405's ticket logger is not on main. The named reason is the abstain exit."""
    from dms_executor.generative_ask import maybe_generative_ask

    assert structural_grant_stop("sql_dialect_unknown")
    assert structural_grant_stop("sql_relation_unresolved")
    submits: list[str] = []

    def _submit(sql: str) -> Any:
        submits.append(sql)
        raise AssertionError("must not submit")

    def _ledger(_payload: dict[str, Any]) -> Any:
        raise AssertionError("must not append")

    env = maybe_generative_ask(
        Q,
        grantable={"orders"},
        compute=lambda _c: {
            "query_sql": "SELECT 1 FROM orders",
            "plan_source": "ontology_plan",
        },
        submit=_submit,
        ledger_append=_ledger,
        dialect="no-such-engine",
    )
    assert env is not None
    assert env["abstained"] is True
    assert submits == []
    assert "sql_dialect_unknown" in _reason(env)
    env = maybe_generative_ask(
        Q,
        grantable={"orders"},
        compute=lambda _c: {
            "query_sql": "SELECT * FROM ?",
            "plan_source": "ontology_plan",
        },
        submit=_submit,
        ledger_append=_ledger,
        dialect="duckdb",
    )
    assert env is not None
    assert env["abstained"] is True
    assert submits == []
    assert "sql_relation_unresolved" in _reason(env)


def test_postgres_unquoted_serves_and_quoted_schema_refuses() -> None:
    granted = {"src_a.orders"}
    assert (
        serve_gap("SELECT 1 FROM SRC_A.ORDERS", grantable=granted, dialect="postgres")
        is None
    )
    assert (
        serve_gap(
            'SELECT 1 FROM "SRC_A".orders', grantable=granted, dialect="postgres"
        )
        == "sql_relation_not_granted"
    )


def test_snowflake_unquoted_serves_and_quoted_lower_refuses() -> None:
    granted = {"src_a.orders"}
    assert (
        serve_gap("SELECT 1 FROM src_a.orders", grantable=granted, dialect="snowflake")
        is None
    )
    assert (
        serve_gap(
            'SELECT 1 FROM "src_a"."orders"',
            grantable=granted,
            dialect="snowflake",
        )
        == "sql_relation_not_granted"
    )


def test_duckdb_quoted_schema_matches_the_engine(tmp_path: Path) -> None:
    """DuckDB resolves \"SRC_A\".orders to src_a.orders. The checker must serve it."""
    con = duckdb.connect(str(tmp_path / "fold.duckdb"))
    try:
        con.execute("CREATE SCHEMA src_a")
        con.execute("CREATE TABLE src_a.orders (n INTEGER)")
        con.execute("INSERT INTO src_a.orders VALUES (7)")
        assert con.execute('SELECT n FROM "SRC_A".orders').fetchall() == [(7,)]
    finally:
        con.close()
    sql = 'SELECT n FROM "SRC_A".orders'
    assert serve_gap(sql, grantable={"src_a.orders"}, dialect="duckdb") is None
    assert serve_gap('SELECT n FROM "orders"', grantable={"orders"}, dialect="duckdb") is None


def test_postgres_quoted_schema_refuses_shortlist_and_serve() -> None:
    """Quoted mixed case is a different Postgres schema. Both checks refuse it."""
    from dms_executor.schema_context import build_schema_context

    granted = {"src_a.orders"}
    refused = '"SRC_A".orders'
    prompt = build_schema_context(
        "how many qwestbeta alphagrant orders",
        {
            "dialect": "postgres",
            "datasets": [
                {
                    "name": "src_a.orders",
                    "columns": [
                        {
                            "name": "category",
                            "type": "varchar",
                            "distinct": 1,
                            "values": ["ALPHAGRANT"],
                        }
                    ],
                },
                {
                    "name": refused,
                    "columns": [
                        {
                            "name": "category",
                            "type": "varchar",
                            "distinct": 1,
                            "values": ["QWESTBETA"],
                        }
                    ],
                },
            ],
        },
        grantable=granted,
    ).prompt
    assert "src_a.orders" in prompt
    assert refused not in prompt
    assert "SRC_A" not in prompt
    assert "QWESTBETA" not in prompt
    assert "qwestbeta" not in prompt
    assert "FILTER HINTS" in prompt
    assert "alphagrant" in prompt
    assert (
        serve_gap(
            'SELECT 1 FROM "SRC_A".orders',
            grantable=granted,
            dialect="postgres",
        )
        == "sql_relation_not_granted"
    )


_SHEET_SQL = 'SELECT 1 FROM bronze."sheet_x"'
_SHEET_NAME = 'bronze."sheet_x"'
# On 19eb1f59 bronze_gap allowed each of these for bronze."sheet_x".
_SHEET_GRANTS = ("other_schema.sheet_x", "warehouse_sheet_x", "SHEET_X")


@pytest.mark.parametrize("grant", _SHEET_GRANTS)
def test_sheet_grant_shapes_refuse_on_both_paths(grant: str) -> None:
    """One checker. The name path and the SQL path both refuse these grants."""
    from dms_executor.grant_struct import relation_gap

    grants = {grant}
    assert serve_gap(_SHEET_SQL, grantable=grants, dialect="duckdb") == (
        "sql_relation_not_granted"
    )
    assert relation_gap(_SHEET_NAME, grantable=grants, dialect="duckdb") == (
        "sql_relation_not_granted"
    )


def test_qualified_bronze_sheet_serves_when_that_relation_is_granted() -> None:
    from dms_executor.grant_struct import relation_gap

    grants = {"bronze.sheet_x"}
    assert serve_gap(_SHEET_SQL, grantable=grants, dialect="duckdb") is None
    assert relation_gap("bronze.sheet_x", grantable=grants, dialect="duckdb") is None


def test_postgres_schema_without_dialect_is_unknown() -> None:
    """No dialect is sql_dialect_unknown. DuckDB must not fold a Postgres name.

    Fails on 19eb1f59: a blank dialect fell back to duckdb and granted
    ``"SRC_A".orders`` from ``src_a.orders``.
    """
    from dms_executor.schema_context import build_schema_context

    granted = {"src_a.orders"}
    prompt = build_schema_context(
        "how many qwestbeta orders",
        {
            "datasets": [
                {
                    "name": "src_a.orders",
                    "columns": [
                        {
                            "name": "n",
                            "type": "integer",
                            "distinct": 1,
                            "values": ["ALPHAGRANT"],
                        }
                    ],
                },
                {
                    "name": '"SRC_A".orders',
                    "columns": [
                        {
                            "name": "category",
                            "type": "varchar",
                            "distinct": 1,
                            "values": ["QWESTBETA"],
                        }
                    ],
                },
            ]
        },
        grantable=granted,
    ).prompt
    assert "sql_dialect_unknown" in prompt
    body = prompt.split("sql_dialect_unknown", 1)[-1]
    assert "src_a.orders" not in body
    assert "SRC_A" not in prompt
    assert "QWESTBETA" not in prompt
    assert "qwestbeta" not in prompt
    assert "ALPHAGRANT" not in prompt
    assert serve_gap(
        'SELECT 1 FROM "SRC_A".orders', grantable=granted, dialect=""
    ) == "sql_dialect_unknown"
    assert serve_gap(
        "SELECT 1 FROM src_a.orders", grantable=granted, dialect="   "
    ) == "sql_dialect_unknown"
