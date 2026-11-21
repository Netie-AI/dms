"""BIRD-ALLOWLIST-01 (dms#304): fail-closed table allowlist for BIRD scoring.

Synthetic tables and stub asks only. No network, no model, no BIRD row, no
skip, no xfail. Figures here are CI fixtures, not a BIRD score. Not COMPLETE.

The CLI tests at the top fail on the parent (ebe65cc) on their own asserts:
there the scorer sends the question. The rest import the new module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import bird_minidev  # noqa: E402
import score_bird  # noqa: E402
from score_bird import EXIT_BLOCKED, EXIT_CONFIG, EXIT_PASS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "tests" / "fixtures" / "bird_minidev" / "synthetic.json"
URL = "http://127.0.0.1:1"
SHA = "878f664d1d920bee99b3859285dd669b86edc16945288a5251c2fa66fae0c330"
# The ten tables the scan flagged, as dms#304 names them.
ISSUE_FLAGGED = {
    "schools",
    "member",
    "client",
    "patient",
    "player",
    "drivers",
    "users",
    "posts",
    "posthistory",
    "comments",
}


def _mod() -> Any:
    import bird_allowlist

    return bird_allowlist


def _allow(
    tables: set[str],
    columns: set[tuple[str, str]] | None = None,
) -> Any:
    mod = _mod()
    return mod.TableAllowlist(
        tables=frozenset(tables),
        held=frozenset({"trans", "yearmonth"}),
        flagged=mod.flagged_tables(),
        excluded_columns=frozenset(columns or set()),
        scan_sha256=SHA,
        zero_hit_tables=31,
        path="test",
    )


class _Spy:
    """Stands in for POST /v1/chat/ask. Records every question it is sent."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.asked: list[str] = []
        self.rows = rows

    def __call__(self, _base: str, question: str, _space: str, _timeout: float) -> dict[str, Any]:
        return self.ask(question)

    def ask(self, question: str) -> dict[str, Any]:
        self.asked.append(question)
        if self.rows is None:
            return {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"}
        return {"badge": "L0_CERTIFIED", "abstained": False, "rows": self.rows, "text": "ok"}


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    bronze: tuple[str, list[str]],
    rows: list[dict[str, Any]] | None = None,
) -> _Spy:
    spy = _Spy(rows)
    monkeypatch.setattr(score_bird, "_ask_live", spy)
    monkeypatch.setattr(score_bird, "fetch_bronze", lambda *a, **k: bronze)
    monkeypatch.setenv("DMS_SCORE_DIR", str(tmp_path / "art"))
    return spy


def _pack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gold: dict[str, str]) -> None:
    data = yaml.safe_load(score_bird.DEFAULT_PACK.read_text(encoding="utf-8"))
    data["questions"] = [c for c in data["questions"] if c["id"] in gold]
    for case in data["questions"]:
        case["gold_sql"] = gold[case["id"]]
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    real = score_bird.load_bird
    monkeypatch.setattr(score_bird, "load_bird", lambda *a, **k: real(path))


def _minidev_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.write_bytes(b"")
    monkeypatch.setenv("CORTEX_FREEROUTE_LEARN", "0")
    monkeypatch.setenv("CORTEX_ROUTE_STORE", str(store))
    monkeypatch.setenv("BIRD_PG_DSN", "host=127.0.0.1 port=1 connect_timeout=1")


# --- CLI: these fail on the parent on their own asserts ----------------------


def test_cli_live_flagged_grant_blocks_before_any_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender", "bronze.public_schools"]))
    rc = score_bird.main(["--live", "--url", URL])
    out = capsys.readouterr().out
    assert spy.asked == [], "a question was sent while a flagged table was grantable"
    assert rc == EXIT_BLOCKED, out
    assert "preflight\tBLOCKED" in out and "bronze.public_schools" in out
    assert "PASS" not in out


def test_cli_live_unreadable_grants_block_before_any_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = _wire(monkeypatch, tmp_path, ("snapshot", []))
    rc = score_bird.main(["--live", "--url", URL])
    out = capsys.readouterr().out
    assert spy.asked == [], "a question was sent without reading the Space's tables"
    assert rc == EXIT_BLOCKED, out
    assert "preflight\tBLOCKED" in out


def test_cli_live_unlisted_table_is_a_named_abstain_and_is_never_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]), rows=[{"n": 3}])
    _pack(
        monkeypatch,
        tmp_path,
        {
            "gender_row_count": "SELECT COUNT(*) AS n FROM client",
            "gender_distinct": "SELEC nope",
        },
    )
    rc = score_bird.main(["--live", "--url", URL])
    out = capsys.readouterr().out
    assert spy.asked == [], "a question on an unlisted table reached the ask"
    assert "gender_row_count\tlive\tABSTAIN\tABSTAIN\texcluded_table:client" in out
    assert "gender_distinct\tlive\tABSTAIN\tABSTAIN\texcluded_table:(unparsed)" in out
    assert rc == EXIT_BLOCKED, out
    assert "VERDICT: NOT SCORED" in out and "PASS" not in out
    art = json.loads((tmp_path / "art" / "score_bird.json").read_text(encoding="utf-8"))
    assert art["passed"] is False
    assert art["allowlist"]["applied"] is True
    assert art["allowlist"]["n"] == 2 and art["allowlist"]["n_without_excluded"] == 0
    assert art["allowlist"]["excluded_by_table"] == {"(unparsed)": 1, "client": 1}
    assert art["generative"]["abstain"] == 2 and art["generative"]["n"] == 2
    assert "not comparable with 73/75" in out


def test_cli_minidev_live_flagged_grant_blocks_before_any_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_Users"]))
    _minidev_env(monkeypatch, tmp_path)
    rc = score_bird.main(["--minidev", str(SYNTHETIC), "--live", "--limit", "2", "--url", URL])
    out = capsys.readouterr().out
    assert spy.asked == [], "Mini-Dev sent a question while a flagged table was grantable"
    assert rc == EXIT_BLOCKED, out
    assert "preflight\tBLOCKED" in out


def test_cli_minidev_live_unlisted_tables_abstain_by_name_and_stay_in_n(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]), rows=[{"n": 2}])
    _minidev_env(monkeypatch, tmp_path)
    rc = score_bird.main(["--minidev", str(SYNTHETIC), "--live", "--limit", "4", "--url", URL])
    out = capsys.readouterr().out
    assert spy.asked == [], "Mini-Dev sent a question on an unlisted table"
    art = json.loads((tmp_path / "art" / "score_bird_minidev.json").read_text(encoding="utf-8"))
    assert art["summary"]["n"] == 4, "an excluded question left n"
    assert art["summary"]["right"] == 0 and art["summary"]["wrong"] == 0
    assert [c["verdict"] for c in art["cases"]] == ["ABSTAIN"] * 4
    assert {c["reason"] for c in art["cases"]} == {
        "excluded_table:widgets",
        "excluded_table:missing_table",
    }
    assert art["allowlist"]["n_without_excluded"] == 0 and art["allowlist"]["excluded"] == 4
    assert rc == EXIT_BLOCKED, out
    assert "VERDICT: NOT SCORED" in out
    assert "not comparable with 73/75" in out


# --- the committed file ------------------------------------------------------


def test_committed_allowlist_loads_and_lists_no_flagged_or_held_table() -> None:
    mod = _mod()
    allow = mod.load_table_allowlist()
    assert allow.scan_sha256 == SHA
    assert allow.zero_hit_tables == 31
    assert allow.flagged == ISSUE_FLAGGED
    assert allow.held == {"trans", "yearmonth"}
    assert not allow.tables & (allow.flagged | allow.held)
    assert len(allow.tables) <= allow.zero_hit_tables


def test_committed_allowlist_holds_names_only() -> None:
    mod = _mod()
    data = yaml.safe_load(mod.DEFAULT_ALLOWLIST.read_text(encoding="utf-8"))
    assert set(data) == {"measure", "scan", "tables", "held", "excluded_columns"}
    assert set(data["scan"]) == {"file", "sha256", "zero_hit_tables"}


def _write(tmp_path: Path, **change: Any) -> Path:
    data: dict[str, Any] = {
        "measure": "bird_allowlist_v1",
        "scan": {"file": "pii_counts.csv", "sha256": SHA, "zero_hit_tables": 31},
        "tables": ["bronze.public_gender", "bronze.public_superhero"],
        "held": ["bronze.public_trans", "bronze.public_yearmonth"],
        "excluded_columns": ["bronze.public_superhero.full_name"],
    }
    for key, value in change.items():
        if key in data["scan"]:
            data["scan"][key] = value
        else:
            data[key] = value
    path = tmp_path / "allow.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_loader_accepts_a_well_formed_file(tmp_path: Path) -> None:
    allow = _mod().load_table_allowlist(_write(tmp_path))
    assert allow.tables == {"gender", "superhero"}
    assert allow.excluded_columns == {("superhero", "full_name")}


@pytest.mark.parametrize(
    "change",
    [
        {"tables": ["bronze.public_gender", "bronze.public_client"]},  # flagged by the scan
        {"tables": ["bronze.public_gender", "bronze.public_trans"]},  # held
        {"tables": [f"bronze.public_t{i:02d}" for i in range(32)]},  # more than the scan's 31
        {"tables": ["bronze.public_gender", "bronze.public_gender"]},
        {"tables": ["gender"]},
        {"tables": ["bronze.public_Gender"]},
        {"tables": ["bronze.public_"]},
        {"tables": ["bronze.public_public_schools"]},  # bare key would be the flagged table
        {"tables": ["bronze.public_public_gender"]},
        {"tables": ["bronze.public_gender; drop"]},
        {"tables": "bronze.public_gender"},
        {"tables": [7]},
        {"sha256": "878f664d"},
        {"sha256": SHA.upper()},
        {"zero_hit_tables": True},
        {"zero_hit_tables": "31"},
        {"measure": "bird_73_75"},
        {"excluded_columns": ["superhero.full_name"]},
        {"excluded_columns": ["bronze.public_superhero"]},
        {"excluded_columns": ["bronze.public_superhero.Full Name"]},
        {"scan": None},
    ],
)
def test_loader_refuses_a_file_it_cannot_trust(tmp_path: Path, change: dict[str, Any]) -> None:
    mod = _mod()
    with pytest.raises(mod.AllowlistError):
        mod.load_table_allowlist(_write(tmp_path, **change))


def test_loader_refuses_a_missing_file_and_a_scan_fixture_with_no_flagged_table(
    tmp_path: Path,
) -> None:
    mod = _mod()
    with pytest.raises(mod.AllowlistError):
        mod.load_table_allowlist(tmp_path / "absent.yaml")
    empty = tmp_path / "flagged.csv"
    empty.write_text("db,table,column,pattern,match_count\n", encoding="utf-8")
    with pytest.raises(mod.AllowlistError):
        mod.load_table_allowlist(_write(tmp_path), flagged_csv=empty)
    with pytest.raises(mod.AllowlistError):
        mod.load_table_allowlist(_write(tmp_path), flagged_csv=tmp_path / "absent.csv")


def test_loader_refuses_a_key_written_twice(tmp_path: Path) -> None:
    mod = _mod()
    text = _write(tmp_path).read_text(encoding="utf-8")
    twice = tmp_path / "twice.yaml"
    twice.write_text(text + "tables:\n- bronze.public_gender\n", encoding="utf-8")
    with pytest.raises(mod.AllowlistError):
        mod.load_table_allowlist(twice)
    nested = tmp_path / "nested.yaml"
    nested.write_text(
        text.replace("zero_hit_tables: 31", "zero_hit_tables: 31\n  zero_hit_tables: 99"),
        encoding="utf-8",
    )
    assert "zero_hit_tables: 99" in nested.read_text(encoding="utf-8")
    with pytest.raises(mod.AllowlistError):
        mod.load_table_allowlist(nested)


def test_cli_unusable_allowlist_is_config_not_a_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _mod()
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]))

    def _broken() -> Any:
        raise mod.AllowlistError("planted")

    monkeypatch.setattr(score_bird, "load_table_allowlist", _broken)
    monkeypatch.setattr(bird_minidev, "load_table_allowlist", _broken)
    assert score_bird.main(["--live", "--url", URL]) == EXIT_CONFIG
    assert score_bird.main(["--ab"]) == EXIT_CONFIG
    _minidev_env(monkeypatch, tmp_path)
    assert score_bird.main(["--minidev", str(SYNTHETIC), "--live", "--limit", "1"]) == EXIT_CONFIG
    assert spy.asked == []
    assert "VERDICT: CONFIG" in capsys.readouterr().out


# --- table rule: every relation is listed, or the question is excluded -------

ALLOWED = {"gender", "superhero", "account"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM gender",
        "SELECT COUNT(*) FROM public.gender",
        "SELECT COUNT(*) FROM GENDER g JOIN Account a ON a.id = g.id",
        "WITH users AS (SELECT id FROM gender) SELECT COUNT(*) FROM users",
        "SELECT id FROM gender UNION SELECT id FROM superhero",
        "SELECT (SELECT MAX(id) FROM account) AS top FROM gender",
        "SELECT * FROM bronze.public_gender",
        "SELECT CAST(g.id AS REAL) / NULLIF(COUNT(*), 0) FROM gender AS g GROUP BY g.id",
    ],
)
def test_question_on_listed_tables_only_is_scored(sql: str) -> None:
    assert _mod().classify(sql, _allow(ALLOWED), dialect="postgres") is None


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT COUNT(*) FROM client", "excluded_table:client"),
        ("SELECT COUNT(*) FROM gender g JOIN client c ON c.id = g.id", "excluded_table:client"),
        ("SELECT id FROM gender WHERE id IN (SELECT id FROM users)", "excluded_table:users"),
        ("WITH x AS (SELECT id FROM patient) SELECT id FROM gender", "excluded_table:patient"),
        ("SELECT id FROM gender UNION ALL SELECT id FROM member", "excluded_table:member"),
        ("SELECT (SELECT MAX(id) FROM schools) FROM gender", "excluded_table:schools"),
        ("SELECT id FROM gender, LATERAL (SELECT id FROM posts) p", "excluded_table:posts"),
        ("SELECT id FROM gender NATURAL JOIN comments", "excluded_table:comments"),
        ("SELECT id FROM gender EXCEPT SELECT id FROM drivers", "excluded_table:drivers"),
        ("SELECT id FROM other.gender", "excluded_table:(unparsed)"),
        ("SELECT id FROM bronze.public_client", "excluded_table:client"),
        ('SELECT id FROM "Gender"', "excluded_table:(unparsed)"),
        ('SELECT id FROM "cli ent"', "excluded_table:(unparsed)"),
        (
            "SELECT query_to_xml('select * from client', true, true, '') FROM gender",
            "excluded_table:(unparsed)",
        ),
        ("SELECT table_to_xml('client', true, true, '') FROM gender", "excluded_table:(unparsed)"),
        (
            "SELECT id FROM gender WHERE cursor_to_xml('c', 1, true, true, '') IS NOT NULL",
            "excluded_table:(unparsed)",
        ),
        ("SELECT id FROM trans", "excluded_table:trans"),
        ("SELECT id FROM yearmonth", "excluded_table:yearmonth"),
        ("WITH gender AS (SELECT id FROM gender) SELECT id FROM client", "excluded_table:client"),
        ("SELECT 1", "excluded_table:(unparsed)"),
        ("SELEC nope FROM gender", "excluded_table:(unparsed)"),
        ("SELECT id FROM gender; SELECT id FROM client", "excluded_table:(unparsed)"),
        ("SELECT * FROM read_csv('x.csv')", "excluded_table:(unparsed)"),
        ("DELETE FROM gender", "excluded_table:(unparsed)"),
        ("SELECT id FROM gender WHERE EXISTS (TABLE client)", "excluded_table:(unparsed)"),
        ("", "excluded_table:(unparsed)"),
        ("   ", "excluded_table:(unparsed)"),
    ],
)
def test_question_on_an_unlisted_or_unsettled_table_is_excluded(sql: str, reason: str) -> None:
    found = _mod().classify(sql, _allow(ALLOWED), dialect="postgres")
    assert found is not None, "scored a question it must exclude"
    assert found.reason == reason
    # DuckDB folds a quoted name, so "Gender" is the listed table there.
    if '"Gender"' not in sql:
        assert _mod().classify(sql, _allow(ALLOWED), dialect="duckdb") is not None


def test_empty_allowlist_excludes_every_question() -> None:
    found = _mod().classify("SELECT COUNT(*) FROM gender", _allow(set()), dialect="postgres")
    assert found is not None and found.reason == "excluded_table:gender"


def test_a_checker_that_fails_excludes_the_question(monkeypatch: pytest.MonkeyPatch) -> None:
    import dms_executor.grant_struct as grant_struct

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("planted")

    monkeypatch.setattr(grant_struct, "serve_gap", _boom)
    found = _mod().classify("SELECT COUNT(*) FROM gender", _allow(ALLOWED), dialect="postgres")
    assert found is not None and found.reason == "excluded_table:(unparsed)"
    assert found.detail == "checker_error:RuntimeError"


def test_an_unknown_dialect_excludes_the_question() -> None:
    found = _mod().classify("SELECT COUNT(*) FROM gender", _allow(ALLOWED), dialect="nosuch")
    assert found is not None and found.reason == "excluded_table:(unparsed)"


# --- column rule -------------------------------------------------------------

COLS = {("superhero", "full_name")}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM superhero",
        "SELECT COUNT(*) FROM superhero",
        "SELECT s.id, g.full_name FROM superhero s JOIN gender g ON g.id = s.id",
        "SELECT g.* FROM superhero s JOIN gender g ON g.id = s.id",
        "SELECT full_name FROM gender",
    ],
)
def test_listed_table_without_the_excluded_column_is_scored(sql: str) -> None:
    assert _mod().classify(sql, _allow(ALLOWED, COLS), dialect="postgres") is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT full_name FROM superhero",
        "SELECT s.full_name FROM superhero s",
        "SELECT superhero.full_name FROM superhero",
        'SELECT "FULL_NAME" FROM superhero',
        "SELECT * FROM superhero",
        "SELECT s.* FROM superhero s",
        "SELECT id FROM superhero WHERE full_name LIKE 'A%'",
        "SELECT COUNT(*) FROM superhero GROUP BY full_name",
        "SELECT id FROM superhero ORDER BY full_name",
        "SELECT COUNT(DISTINCT full_name) FROM superhero",
        "SELECT g.id FROM gender g JOIN superhero s USING (full_name)",
        "SELECT g.id FROM gender g NATURAL JOIN superhero s",
        "SELECT x.n FROM (SELECT full_name AS n FROM superhero) x",
        "SELECT x.full_name FROM (SELECT * FROM superhero) x",
        "WITH x AS (SELECT full_name FROM superhero) SELECT COUNT(*) FROM x",
        "SELECT s FROM superhero s",
        "SELECT COUNT(s) FROM superhero s",
        "SELECT nm FROM superhero AS t(id, nm)",
        "SELECT t.b FROM superhero t(a, b)",
        "SELECT x.nm FROM (SELECT nm FROM superhero AS t(id, nm)) x",
        "SELECT j FROM (superhero a JOIN gender b ON true) AS j",
        'SELECT U&"full\\005Fname" FROM superhero',
        'SELECT "full name" FROM superhero',
        "SELECT id FROM bronze.public_superhero WHERE full_name IS NOT NULL",
    ],
)
def test_question_reading_an_excluded_column_is_a_named_abstain(sql: str) -> None:
    found = _mod().classify(sql, _allow(ALLOWED, COLS), dialect="postgres")
    assert found is not None, "scored a question that reads an excluded column"
    assert found.reason == "excluded_column:superhero.full_name"
    assert found.columns == ("superhero.full_name",)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COLUMNS('full_name') FROM superhero",
        "SELECT COLUMNS('^f') FROM superhero",
        "SELECT COLUMNS(c -> c LIKE 'f%') FROM superhero",
        "SELECT list_value(COLUMNS('full_name')) FROM superhero",
        "SELECT #2 FROM superhero",
        "SELECT id FROM superhero GROUP BY id, #2",
        "SELECT nm FROM superhero AS t(id, nm)",
    ],
)
def test_duckdb_column_read_without_naming_it_is_excluded(sql: str) -> None:
    found = _mod().classify(sql, _allow(ALLOWED, COLS), dialect="duckdb")
    assert found is not None, "scored a question that reads an excluded column"
    assert found.columns == ("superhero.full_name",)


def test_unmodelled_function_is_excluded_and_a_modelled_one_is_scored() -> None:
    mod = _mod()
    allow = _allow(ALLOWED, COLS)
    found = mod.classify("SELECT to_json(s) FROM superhero s", allow, dialect="postgres")
    assert found is not None and found.reason == "excluded_table:(unparsed)"
    assert found.detail == "unmodelled_function:to_json"
    assert found.columns == ("superhero.full_name",)
    hidden = "SELECT query_to_xml('select full_name from superhero', true, true, '') FROM gender"
    assert mod.classify(hidden, allow, dialect="postgres") is not None
    scored = (
        "SELECT ROUND(CAST(SUM(id) AS REAL) / COUNT(*), 2), SUBSTR(gender, 1, 1) "
        "FROM gender GROUP BY gender"
    )
    assert mod.classify(scored, allow, dialect="postgres") is None


def test_table_reason_wins_and_the_column_is_still_counted() -> None:
    mod = _mod()
    found = mod.classify(
        "SELECT s.full_name FROM superhero s JOIN client c ON c.id = s.id",
        _allow(ALLOWED, COLS),
        dialect="postgres",
    )
    assert found is not None and found.reason == "excluded_table:client"
    assert found.columns == ("superhero.full_name",)
    counts = mod.exclusion_counts(
        [
            {"exclusion": found.reason, "excluded_columns": list(found.columns)},
            {
                "exclusion": "excluded_column:superhero.full_name",
                "excluded_columns": ["superhero.full_name"],
            },
            {"verdict": "OK"},
        ]
    )
    assert counts == {
        "n": 3,
        "n_without_excluded": 2 - 1,
        "excluded": 2,
        "gold_error": 0,
        "skipped": 0,
        "excluded_by_table": {"client": 1},
        "excluded_by_column": {"superhero.full_name": 2},
    }


# --- grant preflight ---------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "bronze.public_schools",
        "BRONZE.PUBLIC_SCHOOLS",
        'bronze."public_schools"',
        "public.users",
        "users",
        " bronze.public_postHistory ",
        "bird_minidev#public.client",
        "bronze/public-member",
        "bronze.public_ｕｓｅｒｓ",
        "bronze.public_pätient",
        "main.bronze.public_player",
        "bronze.public_comments",
        "bronze.public_drivers",
        "bronze.public_patient",
        "bronze.public_posts",
    ],
)
def test_preflight_blocks_a_flagged_or_unreadable_name(name: str) -> None:
    mod = _mod()
    assert mod.grant_preflight("ok", ["bronze.public_gender", name], _allow(ALLOWED))


@pytest.mark.parametrize(
    "names",
    [
        ["bronze.public_gender"],
        [
            "bronze.public_superhero",
            "bronze.public_player_attributes",
            "bronze.public_users_badges",
        ],
        ["bronze.public_postlinks", "bronze.public_district", "", None],
    ],
)
def test_preflight_passes_a_space_with_no_flagged_table(names: list[Any]) -> None:
    assert _mod().grant_preflight("ok", names, _allow(ALLOWED)) is None


@pytest.mark.parametrize("status", ["snapshot", "blocked", "", "OK"])
def test_preflight_blocks_when_the_tables_were_not_read(status: str) -> None:
    assert _mod().grant_preflight(status, ["bronze.public_gender"], _allow(ALLOWED))


@pytest.mark.parametrize("names", ["bronze.public_schools", None, {"bronze.public_schools": 1}])
def test_preflight_blocks_names_that_are_not_a_list(names: Any) -> None:
    assert _mod().grant_preflight("ok", names, _allow(ALLOWED))


class _Resp:
    def __init__(self, body: Any) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._body


@pytest.mark.parametrize(
    "body",
    [
        {"tables": [{"table": "bronze.public_gender"}, {"table_name": "bronze.public_schools"}]},
        {"tables": [{"relation": "bronze.public_schools"}]},
        {"tables": [{"name": ""}]},
        [["bronze.public_schools"]],
        [7],
    ],
)
def test_bronze_list_with_a_row_it_cannot_read_is_not_ok(
    monkeypatch: pytest.MonkeyPatch, body: Any
) -> None:
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(body))
    status, names = score_bird.fetch_bronze(URL, score_bird.BIRD_SPACE, 1.0)
    assert status != "ok", "an unreadable row was treated as no table"
    assert _mod().grant_preflight(status, names, _allow(ALLOWED))


def test_bronze_list_it_can_read_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    body = {"tables": [{"table": "bronze.public_gender"}, {"name": "bronze.public_superhero"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(body))
    status, names = score_bird.fetch_bronze(URL, score_bird.BIRD_SPACE, 1.0)
    assert (status, names) == ("ok", ["bronze.public_gender", "bronze.public_superhero"])
    assert _mod().grant_preflight(status, names, _allow(ALLOWED)) is None


def test_live_library_call_runs_the_preflight_without_an_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender", "bronze.public_member"]))
    assert score_bird.live(URL, 1.0, score_bird.BIRD_SPACE) == EXIT_BLOCKED
    assert spy.asked == []
    assert "preflight\tBLOCKED" in capsys.readouterr().out


# --- pack path with a listed table: scored as before -------------------------


def _oracle(tmp_path: Path) -> Path:
    path = tmp_path / "oracle.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE TABLE gender (id INTEGER, gender VARCHAR)")
        con.execute("INSERT INTO gender VALUES (1, 'Male'), (2, 'Female'), (3, 'N/A')")
        con.execute("CREATE TABLE client (id INTEGER)")
        con.execute("INSERT INTO client VALUES (1), (2), (3)")
    finally:
        con.close()
    return path


def test_live_scores_the_listed_question_and_abstains_the_unlisted_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both answers would match their gold rows. Only the listed one may be OK."""
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]), rows=[{"n": 3}])
    _pack(
        monkeypatch,
        tmp_path,
        {
            "gender_row_count": "SELECT COUNT(*) AS n FROM gender",
            "gender_distinct": "SELECT COUNT(*) AS n FROM client",
        },
    )
    rc = score_bird.live(URL, 1.0, score_bird.BIRD_SPACE, _oracle(tmp_path), _allow({"gender"}))
    out = capsys.readouterr().out
    assert spy.asked == ["How many rows are in the gender table?"]
    assert "gender_row_count\tlive\tOK" in out
    assert "gender_distinct\tlive\tABSTAIN\tABSTAIN\texcluded_table:client" in out
    assert rc == EXIT_PASS, out
    art = json.loads((tmp_path / "art" / "score_bird.json").read_text(encoding="utf-8"))
    assert art["generative"]["ok"] == 1 and art["generative"]["abstain"] == 1
    assert art["generative"]["n"] == 2 and art["generative"]["answered"] == 1
    assert art["allowlist"]["n"] == 2 and art["allowlist"]["n_without_excluded"] == 1
    assert art["allowlist"]["excluded_by_table"] == {"client": 1}
    assert "n=2 n_without_excluded=1 excluded=1" in out


def test_live_with_an_unsent_refuse_trap_is_not_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ungated, the green answer on the trap is WRONG. Gated, the trap is not
    sent, so the run must not read as PASS."""
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]), rows=[{"n": 3}])
    data = yaml.safe_load(score_bird.DEFAULT_PACK.read_text(encoding="utf-8"))
    keep = {"gender_row_count", "trap_75_tables"}
    data["questions"] = [c for c in data["questions"] if c["id"] in keep]
    data["questions"][0]["gold_sql"] = "SELECT COUNT(*) AS n FROM gender"
    path = tmp_path / "trap.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    real = score_bird.load_bird
    monkeypatch.setattr(score_bird, "load_bird", lambda *a, **k: real(path))
    oracle = _oracle(tmp_path)
    assert score_bird.live(URL, 1.0, score_bird.BIRD_SPACE, oracle) == score_bird.EXIT_FAIL
    assert len(spy.asked) == 2
    capsys.readouterr()
    rc = score_bird.live(URL, 1.0, score_bird.BIRD_SPACE, oracle, _allow({"gender"}))
    out = capsys.readouterr().out
    assert rc == EXIT_BLOCKED, out
    assert "PASS" not in out and "refuse_traps_unsent=1" in out
    art = json.loads((tmp_path / "art" / "score_bird.json").read_text(encoding="utf-8"))
    assert art["passed"] is False and art["allowlist"]["refuse_traps_unsent"] == 1


def test_live_without_an_allowlist_says_so_in_the_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]), rows=[{"n": 3}])
    _pack(monkeypatch, tmp_path, {"gender_row_count": "SELECT COUNT(*) AS n FROM gender"})
    assert score_bird.live(URL, 1.0, score_bird.BIRD_SPACE, _oracle(tmp_path)) == EXIT_PASS
    art = json.loads((tmp_path / "art" / "score_bird.json").read_text(encoding="utf-8"))
    assert art["allowlist"]["applied"] is False
    assert "table_allowlist=NOT APPLIED" in capsys.readouterr().out


# --- Mini-Dev harness (dms#264) ----------------------------------------------

Q_OK = "SELECT COUNT(*) AS n FROM widgets"
Q_TABLE = "SELECT COUNT(*) AS n FROM widgets w JOIN client c ON c.id = w.id"
Q_COLUMN = "SELECT price AS n FROM widgets"
Q_BOTH = "SELECT w.price AS n FROM widgets w JOIN users u ON u.id = w.id"
Q_BROKEN = "SELEC nope"


def _questions(sqls: list[str], dbs: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "question_id": i,
            "db_id": f"db{i % dbs}",
            "question": f"question {i}",
            "SQL": sql,
            "difficulty": "simple",
        }
        for i, sql in enumerate(sqls)
    ]


def _run(questions: list[dict[str, Any]], spy: _Spy, tmp_path: Path, **kw: Any) -> dict[str, Any]:
    code, report, err = bird_minidev.run_minidev(
        questions,
        ask_fn=spy.ask,
        gold_fn=lambda _sql: ([{"n": 2}], None),
        data_meta={"n": len(questions), "bytes": 0},
        env={"DMS_SCORE_DIR": str(tmp_path)},
        write=False,
        **kw,
    )
    assert code == EXIT_PASS and err is None and report is not None
    return report


def test_minidev_labels_each_question_once_and_never_asks_an_excluded_one(tmp_path: Path) -> None:
    """Every answer would match gold. Only the fully allowed question is RIGHT."""
    spy = _Spy(rows=[{"n": 2}])
    allow = _allow({"widgets"}, {("widgets", "price")})
    report = _run(
        _questions([Q_OK, Q_TABLE, Q_COLUMN, Q_BOTH, Q_BROKEN]), spy, tmp_path, allowlist=allow
    )
    assert spy.asked == ["question 0"]
    cases = report["cases"]
    assert [c["verdict"] for c in cases] == ["OK", "ABSTAIN", "ABSTAIN", "ABSTAIN", "ABSTAIN"]
    assert [c.get("reason") for c in cases] == [
        None,
        "excluded_table:client",
        "excluded_column:widgets.price",
        "excluded_table:users",
        "excluded_table:(unparsed)",
    ]
    assert all(c["got_rows"] == 0 and c["gold_rows"] == 0 for c in cases[1:])
    summary = report["summary"]
    assert summary["n"] == 5 and summary["right"] == 1 and summary["abstain"] == 4
    assert summary["wrong"] == 0 and summary["gold_error"] == 0
    assert summary["exclusions"] == {
        "n": 5,
        "n_without_excluded": 1,
        "excluded": 4,
        "gold_error": 0,
        "skipped": 0,
        "excluded_by_table": {"(unparsed)": 1, "client": 1, "users": 1},
        "excluded_by_column": {"widgets.price": 2},
    }
    assert report["allowlist"]["applied"] is True
    assert report["allowlist"]["comparable_with_73_75"] is False
    assert report["passed"] is False


def test_minidev_total_n_stays_500(tmp_path: Path) -> None:
    sqls = [Q_OK if i % 5 == 0 else (Q_TABLE if i % 2 else Q_COLUMN) for i in range(500)]
    questions = _questions(sqls, dbs=11)
    assert bird_minidev.validate_full_minidev(questions, limit=None) is None
    spy = _Spy(rows=[{"n": 2}])
    allow = _allow({"widgets"}, {("widgets", "price")})
    report = _run(questions, spy, tmp_path, allowlist=allow)
    summary = report["summary"]
    assert summary["n"] == 500
    assert summary["exclusions"]["n"] == 500
    assert summary["exclusions"]["n_without_excluded"] == 100
    assert summary["right"] == 100 and summary["abstain"] == 400 and len(spy.asked) == 100
    assert sum(row["n"] for row in summary["by_db_id"].values()) == 500


def test_compare_refuses_runs_under_different_allowlists(tmp_path: Path) -> None:
    """Always-wrong asks: gating hides two of three WRONGs. Not an improvement."""
    questions = _questions([Q_OK, Q_TABLE, Q_BOTH])
    ungated = _run(questions, _Spy(rows=[{"n": 99}]), tmp_path)
    gated = _run(questions, _Spy(rows=[{"n": 99}]), tmp_path, allowlist=_allow({"widgets"}))
    wider = _run(questions, _Spy(rows=[{"n": 99}]), tmp_path, allowlist=_allow({"widgets", "x"}))
    assert ungated["summary"]["wrong"] == 3 and gated["summary"]["wrong"] == 1
    assert ungated["setup_fingerprint"] == gated["setup_fingerprint"]
    for left, right in ((ungated, gated), (gated, wider)):
        code, body = bird_minidev.compare_runs(left, right, force=False)
        assert code == EXIT_CONFIG and body["refused"] is True
        assert body["reason"] == "allowlist_mismatch"
        code, body = bird_minidev.compare_runs(left, right, force=True)
        assert code == EXIT_PASS and body["comparison"] == "cross-setup"
    code, body = bird_minidev.compare_runs(gated, gated, force=False)
    assert code == EXIT_PASS and body["comparison"] == "same-setup"


def test_cli_minidev_only_gold_errors_left_is_not_measured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One listed question whose gold SQL cannot run, the rest excluded: nothing
    was judged, so the run is NOT SCORED, not MEASURED: WRONG=0."""
    mod = _mod()
    spy = _wire(monkeypatch, tmp_path, ("ok", ["bronze.public_gender"]), rows=[{"n": 2}])
    _minidev_env(monkeypatch, tmp_path)
    monkeypatch.setattr(bird_minidev, "load_table_allowlist", lambda: _allow({"widgets"}))
    rc = score_bird.main(["--minidev", str(SYNTHETIC), "--live", "--limit", "4", "--url", URL])
    out = capsys.readouterr().out
    assert len(spy.asked) == 3
    art = json.loads((tmp_path / "art" / "score_bird_minidev.json").read_text(encoding="utf-8"))
    assert art["summary"]["gold_error"] == 3 and art["summary"]["n"] == 1
    assert art["allowlist"]["excluded"] == 1
    assert rc == EXIT_BLOCKED, out
    assert "VERDICT: NOT SCORED" in out and "MEASURED" not in out
    assert mod.MEASURE in out


def test_minidev_without_an_allowlist_is_labelled_not_applied(tmp_path: Path) -> None:
    spy = _Spy(rows=[{"n": 2}])
    report = _run(_questions([Q_OK, Q_TABLE]), spy, tmp_path)
    assert len(spy.asked) == 2
    assert report["allowlist"]["applied"] is False
    assert _mod().allowlist_lines(report["allowlist"], report["allowlist"]) == [
        "table_allowlist=NOT APPLIED. Not a BIRD measure. Do not quote."
    ]


def test_self_check_plants_cover_the_allowlist() -> None:
    assert _mod().allowlist_self_check() == []
    assert score_bird.self_check() == EXIT_PASS


def test_self_check_fails_when_an_allowlist_plant_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(score_bird, "allowlist_self_check", lambda: ["planted allowlist failure"])
    assert score_bird.self_check() == score_bird.EXIT_FAIL
    assert "planted allowlist failure" in capsys.readouterr().out


# --- second adversary round (Opus, on c941801): the four holes it confirmed --------


@pytest.mark.parametrize(
    "name",
    [
        "bronze.public_users_1a2b3c4d",  # DMS's own collision rename (bronze._claim_table_name)
        "bronze.schools2",
        "bronze.users__1_",
        "bronze.post_history",
        "BRONZE.PUBLIC_POSTHISTORY_0F0F0F0F",
    ],
)
def test_preflight_blocks_a_flagged_table_under_a_copy_name(name: str) -> None:
    got = _mod().grant_preflight("ok", ["bronze.public_gender", name], _allow(ALLOWED))
    assert got is not None and "flagged" in got and name in got, got


@pytest.mark.parametrize("names", [[], ["", None], ["  "]])
def test_preflight_blocks_a_space_that_lists_no_table(names: list[Any]) -> None:
    got = _mod().grant_preflight("ok", names, _allow(ALLOWED))
    assert got is not None and "no table" in got, got


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "ab" * 32},  # well-formed, but not the scan
        {"held": ["bronze.public_trans"]},  # yearmonth dropped
        {"held": []},
        {"zero_hit_tables": 60},
        {"file": "other_counts.csv"},
    ],
)
def test_loader_refuses_a_file_that_does_not_name_the_pinned_scan(
    tmp_path: Path, change: dict[str, Any]
) -> None:
    with pytest.raises(_mod().AllowlistError):
        _mod().load_table_allowlist(_write(tmp_path, **change))


def test_loader_refuses_a_held_table_listed_after_held_is_emptied(tmp_path: Path) -> None:
    path = _write(tmp_path, held=[], tables=["bronze.public_trans", "bronze.public_yearmonth"])
    with pytest.raises(_mod().AllowlistError):
        _mod().load_table_allowlist(path)


def test_counts_drop_gold_errors_and_count_every_unlisted_table() -> None:
    mod = _mod()
    both = mod.classify(
        "SELECT COUNT(*) FROM users u JOIN posts p ON p.owner = u.id", _allow(ALLOWED),
        dialect="duckdb",
    )
    assert both is not None and set(both.tables) == {"users", "posts"}, both
    rows = [
        {"exclusion": both.reason, "excluded_tables": list(both.tables), "verdict": "ABSTAIN"},
        {"verdict": "GOLD_ERROR"},
        {"verdict": "OK"},
    ]
    counts = mod.exclusion_counts(rows)
    assert counts["n"] == 3 and counts["excluded"] == 1 and counts["gold_error"] == 1
    assert counts["n_without_excluded"] == 1
    assert counts["excluded_by_table"] == {"posts": 1, "users": 1}


# --- second verifier round (Opus, on 70f52b9) ---------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "bronze.dbo_users_8fa81536",  # SQL pull from dbo, collision-renamed
        "bronze.codebase_community_posts_5c1f80f8",
        "bronze.main_users2",
        "bronze.dbo_users__1_",
    ],
)
def test_preflight_blocks_a_flagged_table_pulled_from_another_schema(name: str) -> None:
    got = _mod().grant_preflight("ok", ["bronze.public_gender", name], _allow(ALLOWED))
    assert got is not None and name in got, got


def test_preflight_blocks_a_table_whose_sql_source_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SQL pull lands under a name DMS picks; its source names the table it read."""
    import httpx

    body = {
        "tables": [
            {"table": "bronze.public_gender", "source": "gender.csv"},
            {"table": "bronze.pulled_t1", "source": "sqlserver://h/db#dbo.users"},
        ]
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(body))
    status, names = score_bird.fetch_bronze(URL, score_bird.BIRD_SPACE, 1.0)
    assert status == "ok" and list(names) == ["bronze.public_gender", "bronze.pulled_t1"]
    got = _mod().grant_preflight(status, names, _allow(ALLOWED))
    assert got is not None and "users" in got, got


@pytest.mark.parametrize(
    "source",
    [
        "mysql://db:3306/california_schools#california_schools.frpm",
        "postgresql://h:5432/users#public.gender",
        "postgresql://posts-db.internal:5432/bird#public.gender",
        "exports/users/gender.csv",
        "C:\\Users\\alice\\bird\\gender.csv",
    ],
)
def test_preflight_reads_only_the_table_a_source_names(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    import httpx

    body = {"tables": [{"table": "bronze.public_gender", "source": source}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(body))
    status, names = score_bird.fetch_bronze(URL, score_bird.BIRD_SPACE, 1.0)
    assert _mod().grant_preflight(status, names, _allow(ALLOWED)) is None


def test_preflight_passes_sources_that_name_no_flagged_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    body = {"tables": [{"table": "bronze.public_gender", "source": "sqlserver://h/db#dbo.gender"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(body))
    status, names = score_bird.fetch_bronze(URL, score_bird.BIRD_SPACE, 1.0)
    assert _mod().grant_preflight(status, names, _allow(ALLOWED)) is None


def test_a_join_of_unlisted_tables_is_labelled_by_table_not_unparsed() -> None:
    got = _mod().classify(
        "SELECT COUNT(*) FROM users u JOIN posts p ON p.owner = u.id",
        _allow(ALLOWED),
        dialect="duckdb",
    )
    assert got is not None and got.reason in {"excluded_table:posts", "excluded_table:users"}, got
    assert set(got.tables) == {"posts", "users"}


@pytest.mark.parametrize(
    ("sql", "label", "tables"),
    [
        (
            "WITH posts AS (SELECT * FROM posts) SELECT * FROM posts JOIN users ON 1=1",
            None,
            {"posts", "users"},
        ),
        ("SELECT * FROM bronze.public_users", "excluded_table:users", {"users"}),
    ],
)
def test_excluded_tables_names_each_table_once(
    sql: str, label: str | None, tables: set[str]
) -> None:
    got = _mod().classify(sql, _allow(ALLOWED), dialect="duckdb")
    assert got is not None and set(got.tables) == tables, got
    if label:
        assert got.reason == label, got


def test_a_cut_down_flagged_fixture_is_refused(tmp_path: Path) -> None:
    src = Path(__file__).parent / "fixtures" / "pii_hold" / "flagged_columns.csv"
    lines = src.read_text(encoding="utf-8").splitlines()
    cut = tmp_path / "flagged_columns.csv"
    cut.write_text(
        "\n".join([lines[0], *[ln for ln in lines[1:] if ",bronze.public_client," in ln]])
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(_mod().AllowlistError):
        _mod().flagged_tables(cut)


def test_a_skipped_case_is_not_counted_as_scored() -> None:
    rows = [{"exact": "SKIP"}, {"exclusion": "excluded_table:users"}, {"exact": "OK"}]
    counts = _mod().exclusion_counts(rows)
    assert counts["skipped"] == 1 and counts["n_without_excluded"] == 1, counts


@pytest.mark.parametrize(
    ("sql", "label", "tables"),
    [
        ('SELECT * FROM "users,posts"', "excluded_table:(unparsed)", None),
        ("SELECT * FROM public_users", "excluded_table:public_users", {"public_users"}),
        ('SELECT * FROM "Users" JOIN posts ON 1=1', "excluded_table:posts", {"posts", "users"}),
        ("SELECT * FROM users JOIN posts ON 1=1", "excluded_table:posts", {"posts", "users"}),
    ],
)
def test_the_label_names_a_table_the_statement_reads(
    sql: str, label: str, tables: set[str] | None
) -> None:
    got = _mod().classify(sql, _allow(ALLOWED), dialect="duckdb")
    assert got is not None and got.reason == label, got
    if tables is not None:
        assert set(got.tables) == tables, got
