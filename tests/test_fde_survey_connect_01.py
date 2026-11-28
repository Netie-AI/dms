"""FDE-SURVEY-CONNECT-01. Survey and connector setup tests.

The live engines are faked here so CI does not need a database. A real SQLite
file exercises the product opener. Nothing in this file is a PASS of accuracy.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "fde"))

from kits import connect as connect_kit  # noqa: E402
from kits import survey as survey_kit  # noqa: E402
from kits._schema import SCHEMA, render_md, rollup, write_run  # noqa: E402
from kits.matrix import ABSENT_KINDS, classify, render_engine_table  # noqa: E402
from kits.probe import (  # noqa: E402
    _WRITE,
    CAP_FIX,
    ColumnStat,
    Facts,
    TableStat,
    collect,
)

ENGINES = ("postgresql", "mysql", "sqlserver", "sqlite")
SECRET = "pw-not-a-real-secret-99"
PLANTED = "sk-planted-receipt-key-0001"
DRIVER_TEXT = "DRIVERTEXT-UNIQUE password authentication failed"


def _rows_for(sql: str, mode: str) -> list[tuple]:
    text = " ".join(str(sql).split())
    lower = text.lower()
    if "@@session.transaction_read_only" in lower or "tx_read_only" in lower:
        return [(1,)]
    if "transaction_read_only" in lower:
        return [("on",)]
    if "query_only" in lower and "=" not in lower:
        return [(0,)] if mode == "writes" else [(1,)]
    if "server_version" in lower or "version()" in lower or "productversion" in lower:
        return [("16.4",)]
    if "sqlite_version" in lower:
        return [("3.45",)]
    if "pg_stat_ssl" in lower:
        return [(True,)]
    if "ssl_cipher" in lower:
        return [("Ssl_cipher", "TLS")]
    if "count(*)" in lower or "count (*)" in lower:
        return [(600_000 if mode == "cap" else 10,)]
    if "rolsuper" in lower or "is_srvrolemember" in lower:
        if mode == "writes":
            return [(1, 1)]
        return [(0, 0)]
    if "privilege_type" in lower:
        return [("INSERT",)] if mode == "writes" else [("SELECT",)]
    if "session_privs" in lower:
        return [(1,)] if mode == "writes" else [(0,)]
    if (
        "reltuples" in lower
        or "table_rows" in lower
        or "sys.tables" in lower
        or "num_rows" in lower
    ):
        if mode == "cap":
            return [("public", "orders", 600_000, 4096)]
        return [("public", "orders", 10, 4096)]
    if "sqlite_master" in lower:
        return [("orders",)]
    if "page_count" in lower:
        return [(4,)]
    if "page_size" in lower:
        return [(4096,)]
    if "pragma_table_info" in lower:
        if " pk " in f" {lower} ":
            return []
        return [("id", "integer")]
    if "column_name" in lower or "all_tab_columns" in lower:
        return [("public", "orders", "id", "integer", 32, 0)]
    return []


class _Fake:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.last = ""
        self.dms_session_readonly = True

    def cursor(self) -> _Fake:
        return self

    def execute(self, sql: str, params: object = None) -> None:
        self.last = str(sql)

    def fetchall(self) -> list[tuple]:
        return _rows_for(self.last, self.mode)

    def fetchone(self) -> tuple | None:
        rows = self.fetchall()
        return rows[0] if rows else None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def _opener(mode: str):
    def _open(**_kwargs: object) -> _Fake:
        return _Fake(mode)

    return _open


def _source(kind: str) -> dict:
    body: dict = {
        "kind": kind,
        "host": "127.0.0.1",
        "port": 1,
        "database": "app",
        "user": "fde_ro",
        "password_env": "FDE_SOURCE_PASSWORD",
        "tls": True,
        "tables": ["orders"],
    }
    return body


def _facts(kind: str, mode: str, database: str = "app") -> Facts:
    source = _source(kind)
    source["database"] = database
    return collect(
        source,
        SECRET,
        opener=_opener(mode),
        probe_network=False,
    )


def _engage(tmp_path: Path, kind: str = "postgresql") -> Path:
    folder = tmp_path / "acme"
    folder.mkdir()
    (folder / "engagement.yaml").write_text(
        "name: acme\n"
        "tenant_id: tenant-1\n"
        "steward: Ada\n"
        "dba: Grace\n"
        "owner: Lin\n"
        "sources:\n"
        f"  - kind: {kind}\n"
        "    host: 127.0.0.1\n"
        "    port: 1\n"
        "    database: app\n"
        "    user: fde_ro\n"
        "    password_env: FDE_SOURCE_PASSWORD\n"
        "    tls: true\n"
        "    tables: [orders]\n",
        encoding="utf-8",
    )
    (folder / "intake.md").write_text(
        "# Use case\n\nSee late orders.\n"
        "# Decision\n\nShip the weekly number.\n"
        "# Stakeholders\n\nSteward Ada, DBA Grace, owner Lin.\n"
        "# PII list\n\nNone in the extract.\n"
        "# Definitions\n\nAn order is a shipped order.\n",
        encoding="utf-8",
    )
    return folder


@pytest.mark.parametrize("value", [None, "true", "01", " 1 ", "1\n"])
def test_flag_refuses_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("DMS_FDE_KIT", raising=False)
    else:
        monkeypatch.setenv("DMS_FDE_KIT", value)
    code = survey_kit.main(["--engagement", str(tmp_path / "missing")])
    assert code == 2
    assert "fde_kit_disabled" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()
    code = connect_kit.main(["--engagement", str(tmp_path / "missing")])
    assert code == 2
    assert "fde_kit_disabled" in capsys.readouterr().err


def test_password_key_in_the_file_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    folder = tmp_path / "acme"
    folder.mkdir()
    (folder / "engagement.yaml").write_text(
        "name: acme\nsources:\n  - kind: postgresql\n    password: nope-not-stored\n",
        encoding="utf-8",
    )
    code = survey_kit.main(["--engagement", str(folder)])
    captured = capsys.readouterr()
    assert code == 2
    assert "password_in_file" in captured.err
    assert "nope-not-stored" not in captured.err
    assert "nope-not-stored" not in captured.out
    assert not (folder / "runs").exists()


def test_same_check_ids_on_each_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SQLITE_SOURCE_ROOT", str(tmp_path))
    (tmp_path / "app").write_bytes(b"")
    survey_ids = []
    connect_ids = []
    for kind in ENGINES:
        database = "app" if kind == "sqlite" else "app"
        facts = _facts(kind, "ok", database=database)
        assert facts.login_code == "", kind
        assert facts.accepted, kind
        survey_ids.append([item["id"] for item in survey_kit.db_checks(facts)])
        source = _source(kind)
        source["database"] = database
        connect_ids.append([item["id"] for item in connect_kit.source_checks(facts, source)])
    assert survey_ids[0] == survey_ids[1] == survey_ids[2] == survey_ids[3]
    assert connect_ids[0] == connect_ids[1] == connect_ids[2] == connect_ids[3]
    assert "K1.04" in survey_ids[0]
    assert connect_ids[0] == [spec[0] for spec in connect_kit._SPECS]


def test_mysql_privilege_sql_matches_a_quoted_grantee() -> None:
    from kits.probe import _MY_PRIV

    assert "REPLACE(GRANTEE, CHAR(39), '') = CURRENT_USER()" in _MY_PRIV


def test_mariadb_uses_the_mysql_driver() -> None:
    driver, accepted, reason = classify("mariadb")
    assert driver == "mysql"
    assert accepted
    assert reason == ""
    for name in ABSENT_KINDS:
        _driver, ok, why = classify(name)
        assert not ok
        assert "no connector" in why


def test_over_cap_is_critical_and_read_only_login_issues_no_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", SECRET)
    facts = _facts("postgresql", "cap")
    assert facts.writes is False
    assert facts.read_only_txn is True
    assert facts.sql
    assert not any(_WRITE.match(statement) for statement in facts.sql)
    cap = next(item for item in survey_kit.db_checks(facts) if item["id"] == "K1.04")
    assert cap["status"] == "FAIL"
    assert cap["severity"] == "critical"
    assert cap["fix"] == CAP_FIX
    assert cap["evidence"]["rows"] == 600_000
    connect_checks = connect_kit.source_checks(facts, _source("postgresql"))
    truncated = next(item for item in connect_checks if item["id"] == "K2.07")
    assert truncated["status"] == "FAIL"
    assert truncated["fix"] == CAP_FIX


def test_insert_privilege_is_not_read_only_and_issues_no_write() -> None:
    facts = _facts("postgresql", "writes")
    assert facts.writes is True
    assert not any(_WRITE.match(statement) for statement in facts.sql)
    joined = "\n".join(facts.sql).lower()
    assert "insert into" not in joined
    assert "update " not in joined
    assert "delete from" not in joined
    connect_checks = connect_kit.source_checks(facts, _source("postgresql"))
    check = next(item for item in connect_checks if item["id"] == "K2.03")
    assert check["status"] == "FAIL"
    assert check["severity"] == "high"
    assert "not read-only" in check["fix"]
    assert check["evidence"]["result"] == "not read-only"


@pytest.mark.parametrize("kind", ["mysql", "sqlserver", "sqlite"])
def test_insert_shape_on_the_other_engines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setenv("DMS_SQLITE_SOURCE_ROOT", str(tmp_path))
    (tmp_path / "app").write_bytes(b"")
    facts = _facts(kind, "writes")
    assert facts.writes is True
    assert not any(_WRITE.match(statement) for statement in facts.sql)
    connect_checks = connect_kit.source_checks(facts, _source(kind))
    check = next(item for item in connect_checks if item["id"] == "K2.03")
    assert check["status"] == "FAIL"
    assert "not read-only" in check["fix"]


def test_wrong_password_hides_the_password_and_driver_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", SECRET)
    monkeypatch.delenv("DMS_CONNECT_API", raising=False)

    def _boom(**_kwargs: object) -> object:
        raise RuntimeError(f"{DRIVER_TEXT} {SECRET} {PLANTED}")

    def _reader(source: dict, password: str, probe_network: bool = True) -> Facts:
        return collect(source, password, opener=_boom, probe_network=False)

    folder = _engage(tmp_path)
    report = connect_kit.run(folder, _load(folder), collect_fn=_reader, probe_network=False)
    login = next(item for item in report["checks"] if item["id"] == "K2.01")
    assert login["title"] == "Validate login"
    assert login["status"] == "FAIL"
    assert login["evidence"]["code"] == "auth"
    blob = _output_text(folder)
    assert SECRET not in blob
    assert PLANTED not in blob
    assert DRIVER_TEXT not in blob
    assert "password authentication" not in blob
    assert "Validate login: FAIL" in blob


def test_unsupported_engine_is_skipped_not_pass() -> None:
    facts = collect(
        {"kind": "snowflake", "host": "db", "database": "app", "user": "u"},
        SECRET,
        probe_network=False,
    )
    checks = connect_kit.source_checks(facts, {"kind": "snowflake"})
    assert checks
    assert all(item["status"] != "PASS" for item in checks)
    assert any("no connector" in json.dumps(item) for item in checks)
    survey_checks = survey_kit.db_checks(facts)
    assert all(item["status"] == "SKIPPED" for item in survey_checks)


def test_flagged_engines_stay_skipped_until_the_digit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DMS_CONNECT_GAUSS", raising=False)
    monkeypatch.delenv("DMS_CONNECT_ORACLE", raising=False)
    monkeypatch.delenv("DMS_SQLITE_SOURCE_ROOT", raising=False)
    for kind, flag in (
        ("gauss", "DMS_CONNECT_GAUSS"),
        ("oracle", "DMS_CONNECT_ORACLE"),
        ("sqlite", "DMS_SQLITE_SOURCE_ROOT"),
    ):
        facts = collect(_source(kind), SECRET, probe_network=False)
        assert facts.accepted is False
        assert flag in facts.skip_reason
        skipped = connect_kit.source_checks(facts, _source(kind))
        assert all(item["status"] != "PASS" for item in skipped)


def test_connect_route_off_is_skipped_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_CONNECT_API", raising=False)
    monkeypatch.delenv("DMS_CONNECT_V2", raising=False)
    check = connect_kit.api_check(_source("postgresql"))
    assert check["status"] == "SKIPPED"
    assert check["status"] != "PASS"
    assert "DMS_CONNECT_API off" in check["evidence"]["reason"]
    monkeypatch.setenv("DMS_CONNECT_API", "true")
    monkeypatch.setenv("DMS_CONNECT_V2", "01")
    again = connect_kit.api_check(_source("postgresql"))
    assert again["status"] == "SKIPPED"
    assert "DMS_CONNECT_V2 off" in again["evidence"]["reason"]


def test_connect_route_does_not_send_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_CONNECT_API", "1")
    monkeypatch.setenv("DMS_CONNECT_V2", "1")
    monkeypatch.setenv("DMS_API_BASE", "http://127.0.0.1:9")
    monkeypatch.setenv("DMS_SERVICE_BEARER", "bearer-not-a-real-token")
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", SECRET)
    seen: list[bytes] = []

    class _Resp:
        status = 200

        def read(self, _n: int) -> bytes:
            return b'{"ok": true, "tables": []}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def _open(request: object, timeout: int = 10) -> _Resp:
        data = getattr(request, "data", None) or b""
        seen.append(data)
        return _Resp()

    source = _source("postgresql")
    source["ov_ref"] = "dms/fde-kid"
    check = connect_kit.api_check(source, opener=_open)
    assert check["status"] == "PASS"
    blob = b" ".join(seen)
    assert SECRET.encode() not in blob
    assert b"password" not in blob.lower()
    assert b"dms/fde-kid" in blob


def test_report_schema_and_markdown_come_from_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    monkeypatch.setenv("FDE_SOURCE_PASSWORD", SECRET)
    folder = _engage(tmp_path)

    def _reader(source: dict, password: str, probe_network: bool = True) -> Facts:
        return Facts(
            kind="postgresql",
            driver_kind="postgresql",
            accepted=True,
            version="16.4",
            reachable=True,
            tables=[TableStat("public", "orders", 10, 100, "catalog")],
            columns=[ColumnStat("public", "orders", "id", "integer", 32, 0)],
            pk_count=1,
            fk_count=0,
            key_path="pg_catalog",
            writes=False,
            read_only_txn=True,
            tls_on=True,
            requested_tls=True,
        )

    report = survey_kit.run(folder, _load(folder), collect_fn=_reader, probe_network=False)
    assert report["schema"] == SCHEMA
    for key in (
        "kit",
        "stage",
        "engagement",
        "tenant_id",
        "dms_sha",
        "started_at",
        "finished_at",
        "flags",
        "status",
        "checks",
        "metrics",
        "artifacts",
        "cost",
    ):
        assert key in report
    assert report["status"] != "PASS" or report["checks"]
    path = next((folder / "runs").rglob("report.json"))
    body = json.loads(path.read_text(encoding="utf-8"))
    markdown = (path.parent / "report.md").read_text(encoding="utf-8")
    assert markdown == render_md(body)
    body["checks"][0]["status"] = "FAIL"
    body["checks"][0]["fix"] = "FROMJSON"
    assert "FROMJSON" in render_md(body)
    assert rollup([]) == "SKIPPED"


def test_scrub_removes_a_password_and_a_planted_key(tmp_path: Path) -> None:
    report = {
        "schema": SCHEMA,
        "kit": "connect",
        "stage": "K2",
        "engagement": "acme",
        "tenant_id": "t",
        "dms_sha": "56f55f84",
        "started_at": "2026-11-28T00:00:00Z",
        "finished_at": "2026-11-28T00:00:01Z",
        "flags": {},
        "status": "FAIL",
        "checks": [
            {
                "id": "K2.01",
                "title": "Validate login",
                "severity": "critical",
                "status": "FAIL",
                "evidence": {"note": f"saw {SECRET} and {PLANTED}"},
                "fix": "The login was refused.",
                "source": "",
            }
        ],
        "metrics": {},
        "artifacts": [],
        "cost": {"ov_calls": 0, "usd": 0.0, "source": "OV /api/usage"},
    }
    write_run(tmp_path, report, [SECRET, PLANTED])
    blob = (tmp_path / "report.json").read_text(encoding="utf-8")
    blob += (tmp_path / "report.md").read_text(encoding="utf-8")
    assert SECRET not in blob
    assert PLANTED not in blob


def test_empty_intake_stays_manual(tmp_path: Path) -> None:
    checks = survey_kit.intake_checks(tmp_path, {})
    assert checks
    assert all(item["status"] == "MANUAL" for item in checks)
    assert all(item["status"] != "PASS" for item in checks)


def test_untyped_names_a_column_and_not_a_cell() -> None:
    facts = Facts(
        kind="postgresql",
        driver_kind="postgresql",
        accepted=True,
        version="16.4",
        tables=[TableStat("public", "orders", 3, 10, "catalog")],
        columns=[
            ColumnStat("public", "orders", "amount", "numeric", None, None),
        ],
        pk_count=1,
        fk_count=0,
        key_path="pg_catalog",
        writes=False,
        read_only_txn=True,
        requested_tls=True,
        tls_on=True,
    )
    connect_checks = connect_kit.source_checks(facts, _source("postgresql"))
    check = next(item for item in connect_checks if item["id"] == "K2.08")
    assert check["status"] == "FAIL"
    blob = json.dumps(check)
    assert "orders.amount" in blob or "public.orders.amount" in blob
    assert "user@example.com" not in blob


def test_runbook_engine_table_matches_the_code() -> None:
    text = (ROOT / "docs" / "fde" / "RUNBOOK.md").read_text(encoding="utf-8")
    start = text.index("<!-- fde-engine-matrix:start -->")
    end = text.index("<!-- fde-engine-matrix:end -->")
    block = text[start:end]
    table = render_engine_table()
    assert table in block
    for name in ABSENT_KINDS:
        assert f"`{name}`" in table
    assert "none yet" not in table


def test_sqlite_file_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "files"
    root.mkdir()
    database = root / "shop.sqlite"
    con = sqlite3.connect(database)
    con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO orders VALUES (1, 'a'), (2, 'b')")
    con.commit()
    con.close()
    monkeypatch.setenv("DMS_FDE_KIT", "1")
    monkeypatch.setenv("DMS_SQLITE_SOURCE_ROOT", str(root))
    monkeypatch.delenv("DMS_CONNECT_API", raising=False)
    folder = tmp_path / "acme"
    folder.mkdir()
    (folder / "engagement.yaml").write_text(
        "name: acme\n"
        "tenant_id: tenant-1\n"
        "sources:\n"
        "  - kind: sqlite\n"
        "    database: shop.sqlite\n"
        "    tables: [orders]\n",
        encoding="utf-8",
    )
    report = connect_kit.run(folder, _load(folder))
    by_id = {item["id"]: item for item in report["checks"]}
    assert by_id["K2.01"]["status"] == "PASS"
    assert by_id["K2.03"]["status"] == "PASS"
    assert by_id["K2.04"]["status"] == "PASS"
    assert by_id["K2.07"]["status"] == "PASS"
    assert by_id["K2.10"]["status"] == "SKIPPED"
    assert "DMS_CONNECT_API off" in json.dumps(by_id["K2.10"])
    receipt = json.loads(
        next((folder / "runs").rglob("receipts/connect.json")).read_text(encoding="utf-8")
    )
    assert receipt["truncated"] == []
    assert "a" not in receipt["untyped"]


def _load(folder: Path) -> dict:
    import yaml

    return yaml.safe_load((folder / "engagement.yaml").read_text(encoding="utf-8"))


def _output_text(folder: Path) -> str:
    parts = []
    for path in (folder / "runs").rglob("*"):
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
