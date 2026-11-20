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
        "scan": {"file": "pii_counts.csv", "sha256": SHA, "zero_hit_tables": 2},
        "tables": ["bronze.public_gender", "bronze.public_superhero"],
        "held": ["bronze.public_trans"],
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
        {"tables": ["bronze.public_gender", "bronze.public_superhero", "bronze.public_hair"]},
        {"tables": ["bronze.public_gender", "bronze.public_gender"]},
        {"tables": ["gender"]},
        {"tables": ["bronze.public_Gender"]},
        {"tables": ["bronze.public_"]},
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
        ("SELECT id FROM other.gender", "excluded_table:gender"),
        ("SELECT id FROM bronze.public_client", "excluded_table:public_client"),
        ('SELECT id FROM "Gender"', "excluded_table:Gender"),
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
        "SELECT to_json(s) FROM superhero s",
        "SELECT id FROM bronze.public_superhero WHERE full_name IS NOT NULL",
    ],
)
def test_question_reading_an_excluded_column_is_a_named_abstain(sql: str) -> None:
    found = _mod().classify(sql, _allow(ALLOWED, COLS), dialect="postgres")
    assert found is not None, "scored a question that reads an excluded column"
    assert found.reason == "excluded_column:superhero.full_name"
    assert found.columns == ("superhero.full_name",)


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
        [],
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
