"""Lane 1 bench harness: offline, fake transport only, no key, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "bench"))

import lane1  # noqa: E402


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    folder = tmp_path_factory.mktemp("lane1_wh")
    con, as_of, _ = lane1.grade52._open_warehouse(folder)
    cases = lane1.load_cases(con, as_of)
    yield con, as_of, cases
    con.close()


def _by_question(cases):
    return {case.question: case for case in cases}


def _case_for(cases, system, messages):
    """The same question text can sit in two Spaces; the granted schema tells them apart."""
    question = lane1._question_of(messages)
    schema = system.split("Granted schema", 1)[1]
    listed = {line[2:].split("(", 1)[0] for line in schema.splitlines() if line.startswith("- ")}
    matches = [c for c in cases if c.question == question and set(c.tables) == listed]
    assert len(matches) == 1, question
    return matches[0]


def _oracle_model(cases):
    def reply(system, messages, *, model, effort):
        case = _case_for(cases, system, messages)
        if case.expected == "refuse" or not case.gold_sql:
            return '{"abstain": "not answerable from the granted tables"}'
        return lane1.json.dumps({"sql": case.gold_sql})

    return reply


def _rows(tmp_path, transport, **kw):
    kw.setdefault("models", ["fake-model"])
    kw.setdefault("strategies", ["s0"])
    kw.setdefault("efforts", ["medium"])
    kw.setdefault("rounds", 1)
    return lane1.run(transport, workdir=tmp_path, **kw)


def test_pack_shape(pack):
    _, _, cases = pack
    assert len(cases) == 52
    kinds = {case.expected for case in cases}
    assert kinds == {"answer", "refuse"}
    assert all(case.tables for case in cases)


def test_oracle_model_scores_wrong_zero_and_refuses_traps(pack, tmp_path):
    _, _, cases = pack
    rows = _rows(tmp_path, lane1.FakeTransport(_oracle_model(cases)))
    labels = {row["qid"]: row["label"] for row in rows}
    assert sum(1 for v in labels.values() if v == "WRONG") == 0
    for case in cases:
        if case.expected == "refuse":
            assert labels[case.qid] == "ABSTAIN"
        elif case.oracle_error:
            assert labels[case.qid] == "ORACLE_ERROR"
        else:
            assert labels[case.qid] == "CORRECT", case.qid
    cell = lane1.summarise(rows)[0]
    assert cell["wrong"] == 0
    assert cell["precision_on_answered"] == 1.0
    assert cell["wrong_ub95"] == round(3 / cell["answered"], 4)


def test_constant_model_is_wrong_and_serves_traps(pack, tmp_path):
    _, _, cases = pack
    rows = _rows(tmp_path, lane1.FakeTransport(lambda *a, **k: '{"sql": "SELECT 1 AS n"}'))
    trap = next(r for r in rows if r["expected"] == "refuse")
    assert trap["label"] == "WRONG" and trap["wrong_kind"] == "served_trap"
    answer = [r for r in rows if r["expected"] == "answer" and r["label"] != "ORACLE_ERROR"]
    assert answer and all(r["label"] == "WRONG" for r in answer)


def test_ungranted_table_is_wrong(pack, tmp_path):
    _, _, cases = pack
    ops = next(c for c in cases if c.space == "ops" and "alerts" not in c.tables)
    fake = lane1.FakeTransport({ops.question: '{"sql": "SELECT * FROM alerts"}'})
    rows = _rows(tmp_path, fake, only=[ops.qid])
    assert rows[0]["label"] == "WRONG"
    assert rows[0]["wrong_kind"] == "ungranted_table"


def test_non_select_and_garbage_are_invalid(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    for reply in ('{"sql": "DROP TABLE inventory"}', "no json here", '{"sql": "SELECT FROM"}'):
        rows = _rows(tmp_path, lane1.FakeTransport({case.question: reply}), only=[case.qid])
        assert rows[0]["label"] == "INVALID", reply


def test_rate_limit_is_its_own_label(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    rows = _rows(tmp_path, lane1.FakeTransport({case.question: ["RATE_LIMIT"]}), only=[case.qid])
    assert rows[0]["label"] == "RATE_LIMIT"
    assert rows[0]["model_calls_ok"] == 0


def test_s3_repairs_from_engine_error_only(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    script = {
        case.question: [
            '{"sql": "SELECT no_such_col FROM inventory"}',
            lane1.json.dumps({"sql": case.gold_sql}),
        ]
    }
    seen: list[str] = []

    class Spy(lane1.FakeTransport):
        def complete(self, system, messages, *, model, effort):
            seen.append(messages[-1]["content"])
            return super().complete(system, messages, model=model, effort=effort)

    rows = _rows(tmp_path, Spy(script), strategies=["s3"], only=[case.qid])
    assert rows[0]["label"] == "CORRECT"
    assert rows[0]["model_calls"] == 2
    assert "failed in DuckDB" in seen[-1]
    assert case.gold_sql not in seen[-1]


def test_s4_disagreement_abstains(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    script = {case.question: [lane1.json.dumps({"sql": case.gold_sql}), '{"sql": "SELECT 1 AS n"}']}
    rows = _rows(tmp_path, lane1.FakeTransport(script), strategies=["s4"], only=[case.qid])
    assert rows[0]["label"] == "ABSTAIN"
    assert rows[0]["abstain_reason"] == "vote_disagree"
    assert rows[0]["model_calls"] == 2


def test_s2_refuses_without_pool(pack, tmp_path):
    _, _, cases = pack
    with pytest.raises(SystemExit):
        _rows(tmp_path, lane1.FakeTransport({}), strategies=["s2"], only=[cases[0].qid])


def test_record_then_replay_is_identical(pack, tmp_path):
    _, _, cases = pack
    record = tmp_path / "record"
    first = _rows(
        tmp_path, lane1.RecordingTransport(lane1.FakeTransport(_oracle_model(cases)), record)
    )
    again = _rows(tmp_path, lane1.ReplayTransport(record))
    keep = ("qid", "label", "row_hash", "sql")
    assert [{k: r[k] for k in keep} for r in first] == [{k: r[k] for k in keep} for r in again]


def test_replay_never_invents_a_reply(pack, tmp_path):
    _, _, cases = pack
    with pytest.raises(FileNotFoundError):
        _rows(tmp_path, lane1.ReplayTransport(tmp_path / "empty"), only=[cases[0].qid])


def test_prompt_carries_no_oracle_sql_and_quarantine_detects_it(pack):
    con, as_of, cases = pack
    for case in cases:
        system = lane1.system_prompt(con, case, as_of)
        assert lane1.quarantine_violation(system, case, cases) == ""
    leaky = next(c for c in cases if c.gold_sql)
    assert lane1.quarantine_violation(leaky.gold_sql or "", cases[0], cases)


def test_prompt_lists_only_granted_tables(pack):
    con, as_of, cases = pack
    ops = next(c for c in cases if c.space == "ops")
    system = lane1.system_prompt(con, ops, as_of)
    assert "- shipments(" in system
    assert "- alerts(" not in system and "- transactions(" not in system


def test_pin_mismatch_is_invalid(pack):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    attempt = lane1.Attempt(kind="sql", rows=list(case.gold or []), served_model="other")
    assert lane1.grade(case, attempt, pinned="pinned-model")["label"] == "INVALID"


def test_live_transports_need_explicit_egress(tmp_path):
    for transport in ("anthropic", "openvault"):
        with pytest.raises(SystemExit):
            lane1.main(["--transport", transport, "--models", "m", "--out", str(tmp_path)])


def test_upper_bound():
    assert lane1.clopper_pearson_upper(0, 52) == pytest.approx(3 / 52)
    assert lane1.clopper_pearson_upper(1, 10) == pytest.approx(0.3942, abs=1e-3)


def test_replay_keeps_each_round_apart(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    script = {
        case.question: [
            lane1.json.dumps({"sql": case.gold_sql}),
            '{"sql": "SELECT 1 AS n"}',
            '{"abstain": "unsure"}',
        ]
    }
    record = tmp_path / "rounds"
    live = lane1.RecordingTransport(lane1.FakeTransport(script), record)
    first = _rows(tmp_path, live, rounds=3, only=[case.qid])
    again = _rows(tmp_path, lane1.ReplayTransport(record), rounds=3, only=[case.qid])
    assert [r["label"] for r in first] == ["CORRECT", "WRONG", "ABSTAIN"]
    assert [r["label"] for r in again] == [r["label"] for r in first]


def test_values_context_lists_stored_codes_and_stays_quarantined(pack):
    con, as_of, cases = pack
    finance = next(c for c in cases if c.space == "finance" and "transactions" in c.tables)
    plain = lane1.system_prompt(con, finance, as_of)
    rich = lane1.system_prompt(con, finance, as_of, "values")
    assert "values:" not in plain
    assert "transactions.txn_type values: 'inbound', 'outbound'" in rich
    for case in cases:
        assert (
            lane1.quarantine_violation(lane1.system_prompt(con, case, as_of, "values"), case, cases)
            == ""
        )


def test_context_is_its_own_cell(pack, tmp_path):
    _, _, cases = pack
    fake = lane1.FakeTransport(_oracle_model(cases))
    rows = _rows(tmp_path, fake, only=[cases[0].qid], context="values")
    assert rows[0]["context"] == "values"
    assert lane1.summarise(rows)[0]["context"] == "values"


def test_product_context_is_the_shipping_prompt_and_stays_quarantined(pack, tmp_path):
    con, as_of, cases = pack
    _, _, serving = lane1.grade52._open_warehouse(tmp_path / "wh")
    cache: dict = {}
    for case in cases:
        system = lane1.system_prompt(con, case, as_of, "product", serving=serving, cache=cache)
        assert "DIALECT: duckdb" in system
        assert lane1.quarantine_violation(system, case, cases) == ""
    finance = next(c for c in cases if c.qid == "cq_sales_top3_volume")
    system = lane1.system_prompt(con, finance, as_of, "product", serving=serving, cache=cache)
    # The shipping prompt states how many codes a text column holds, not which.
    assert "txn_type VARCHAR distinct=2" in system
    assert "outbound" not in system


def test_product_context_runs_a_cell(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.qid == "cq_sales_top3_volume")
    fake = lane1.FakeTransport({case.question: lane1.json.dumps({"sql": case.gold_sql})})
    rows = _rows(tmp_path, fake, only=[case.qid], context="product")
    assert rows[0]["label"] == "CORRECT" and rows[0]["context"] == "product"


def test_pinned_as_of_replays_on_a_later_day(pack, tmp_path):
    _, _, cases = pack
    case = next(c for c in cases if c.expected == "answer" and c.gold_sql)
    record = tmp_path / "dated"
    live = lane1.RecordingTransport(lane1.FakeTransport(_oracle_model(cases)), record)
    first = _rows(tmp_path, live, only=[case.qid], as_of="2026-10-30")
    with pytest.raises(FileNotFoundError):
        _rows(tmp_path, lane1.ReplayTransport(record), only=[case.qid], as_of="2026-11-06")
    again = _rows(tmp_path, lane1.ReplayTransport(record), only=[case.qid], as_of="2026-10-30")
    assert first[0]["as_of"] == again[0]["as_of"] == "2026-10-30"
    assert first[0]["label"] == again[0]["label"] == "CORRECT"
