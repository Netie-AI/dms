"""Self-check for the gate-matrix harness: real runs, not mocks of the harness.

A harness that is wrong makes every gap test after it wrong in a way that looks
like a pass (KB R-0007, F-0011). So this file proves, by running them:

  * the control request is genuinely answered through the real ask path
    (HTTP 200, L1_GOVERNED_METRIC, rows, one SQL submit, one ledger append),
  * a pack question in a Space that does not grant it is a named grants-fail
    abstain and never reaches Cortex.ask (CURATED-NO-SILENT-FALLBACK),
  * the recording fake is not a yes-man (a question that does reach ask abstains),
  * ``@gap`` turns a failed assertion into XFAIL and everything else (broken
    fixture, RuntimeError, KeyError, failing control, a gap that closed) into a
    hard FAILURE, checked by running pytest on a temporary inner test file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _harness import (  # noqa: E402
    FINANCE,
    HOSTILE_CELLS,
    WAREHOUSE_OPS,
    ControlFailed,
    assert_envelope,
    control,
    csv_bytes,
    gap,
    hostile_upload_csv,
    require_envelope,
)

REPO = HERE.parents[1]


def _pack():  # type: ignore[no-untyped-def]
    from dms_executor import demo_pack

    return demo_pack


# ---------------------------------------------------------------- the control


def test_openvault_probe_is_pinned_offline_and_the_warehouse_is_not_the_repo_default() -> None:
    import dms_executor
    from dms_executor.demo_warehouse import warehouse_path

    assert dms_executor.probe_openvault(preferred_url=None) == (None, "")
    assert not Path(warehouse_path()).resolve().is_relative_to(REPO)


def test_harness_runs_the_worktree_not_another_checkout() -> None:
    import dms_api
    import dms_executor

    for mod in (dms_api, dms_executor):
        assert Path(mod.__file__).resolve().is_relative_to(REPO), mod.__file__


def test_control_pack_question_is_certified_through_the_fake(harness) -> None:  # type: ignore[no-untyped-def]
    """Control (i): the FINANCE pack question, end to end, as the customer sees it."""
    pack = _pack()
    status, env = harness.ask(pack.SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_ctl")

    assert status == 200, env
    assert_envelope(env)
    assert env["abstained"] is False
    assert env["badge"] == "L1_GOVERNED_METRIC"
    assert env["rows"], "a certified answer returns rows"
    assert {"MY", "SG", "TH"} <= {str(r.get("country")) for r in env["rows"]}
    assert "MY" in env["text"]
    assert any(isinstance(v.get("value"), (int, float)) for v in env["values"])

    cx = harness.cortex
    assert len(cx.bind_submits) == 1, "one session bind"
    assert len(cx.sql_submits) == 1, "exactly one SQL submit"
    assert cx.submitted_sql() == [pack.SPEND_BY_COUNTRY_SQL]
    assert len(cx.appends) == 1, "exactly one ledger append"
    assert cx.ledger_events() == ["ask.governed_metric"]
    assert cx.asks == [], "a pack hit never reaches Cortex.ask"
    assert env["audit_id"] == cx.ledger_entry_id, "audit_id is the entry id, not the hash"
    assert [name for name, _ in cx.calls] == ["submit", "submit", "ledger_append"]


def test_ops_spend_names_grants_fail(harness) -> None:  # type: ignore[no-untyped-def]
    """Warehouse Ops does not grant suppliers.

    The phrase matches. The grants step fails. CURATED-NO-SILENT-FALLBACK
    names that and returns, so a yes-man Cortex.ask cannot certify it.
    """
    status, env = harness.ask(
        _pack().SPEND_BY_COUNTRY_Q, space_id=WAREHOUSE_OPS, session_id="ses_sib"
    )

    assert status == 200, env
    assert_envelope(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert not env["rows"] and not env["values"]
    assert "exact match ok" in env["text"]
    assert "grants fail" in env["text"]
    assert "pack-metric miss" not in env["text"]
    assert harness.cortex.asks == []
    assert harness.cortex.sql_submits == []
    assert harness.cortex.appends == []


def test_default_fake_abstains_when_ask_is_reached(harness) -> None:  # type: ignore[no-untyped-def]
    """The fake is not a yes-man: its default ask abstains.

    ``alerts`` is not a pack phrase. The same harness's gap control already
    shows this question reaches Cortex.ask. The default fake must refuse it.
    """
    status, env = harness.ask(
        "List all open alerts by severity", space_id=FINANCE, session_id="ses_fake"
    )

    assert status == 200, env
    assert_envelope(env)
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"
    assert not env["rows"] and not env["values"]
    assert "Cannot answer from this Space." in env["text"]
    assert len(harness.cortex.asks) == 1
    assert harness.cortex.sql_submits == []
    assert harness.cortex.appends == []


def test_followup_recipe_reuses_the_session(harness) -> None:  # type: ignore[no-untyped-def]
    """Recipe (b): a follow-up in the same session is answered without Cortex.ask."""
    pack = _pack()
    s1, parent = harness.ask(pack.STOCK_BY_CATEGORY_Q, space_id=FINANCE, session_id="ses_f")
    control(s1 == 200 and parent["abstained"] is False, parent["text"])
    asks_before = len(harness.cortex.asks)

    status, env = harness.ask("average of them", space_id=FINANCE, session_id="ses_f")

    assert status == 200, env
    assert_envelope(env)
    assert env["badge"] == "L2_VALIDATED"
    assert len(env["values"]) == 1
    assert "verage" in env["text"]
    assert len(harness.cortex.asks) == asks_before


def test_ledger_payload_recipe(harness) -> None:  # type: ignore[no-untyped-def]
    """Recipe (c): read what DMS handed to ledger_append."""
    status, env = harness.ask(_pack().TOTAL_SPEND_Q, space_id=FINANCE, session_id="ses_led")
    control(status == 200 and env["abstained"] is False, env["text"])

    payloads = harness.cortex.ledger_payloads("ask.governed_metric")
    assert len(payloads) == 1 and isinstance(payloads[0], dict)
    assert harness.cortex.appends[0].event_type == "ask.governed_metric"


# ------------------------------------------------------- configurable fake


def test_fake_is_configurable_rejected_submit_falls_through(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(submit_ok=False)
    status, env = h.ask(_pack().SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_rej")

    assert status == 200, env
    assert_envelope(env)
    assert len(h.cortex.sql_submits) == 1, "the submit was attempted and recorded"
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert not env["rows"]


def test_fake_is_configurable_rows_and_ledger_ids(harness_factory) -> None:  # type: ignore[no-untyped-def]
    rows = [{"country": "MY", "total_spend_myr": 1.25}]
    h = harness_factory(rows=rows, ledger_entry_id="led_custom", ledger_hash="h_custom")
    status, env = h.ask(_pack().SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_cfg")

    assert status == 200, env
    assert_envelope(env)
    assert env["rows"] == rows
    assert env["audit_id"] == "led_custom"


def test_fake_raises_on_submit_is_recorded_and_never_certifies(harness_factory) -> None:  # type: ignore[no-untyped-def]
    h = harness_factory(submit_raises=RuntimeError("engine down"))
    status, env = h.ask(_pack().SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_raise")

    assert status == 200, env
    assert_envelope(env)
    assert len(h.cortex.sql_submits) == 1, "the raising call is on the record"
    assert env["abstained"] is True and env["badge"] == "ABSTAIN"
    assert not env["rows"] and not env["values"]
    assert h.cortex.appends == [], "no ledger entry for an answer that was not made"


# ------------------------------------------------------ upload and flags


def test_hostile_upload_recipe_is_granted_to_one_space_only(harness) -> None:  # type: ignore[no-untyped-def]
    """Recipe (d): the upload is registered to FINANCE and is not grantable in Ops."""
    receipt = harness.upload("hostile.csv", hostile_upload_csv(), space_id=FINANCE)
    table = receipt.table

    assert table and table.startswith("bronze.")
    assert table in harness.executor.grantable_tables(space_id=FINANCE)
    assert table not in harness.executor.grantable_tables(space_id=WAREHOUSE_OPS)
    # The payloads must reach the table verbatim, or a gap test built on them
    # would be testing a sanitised copy.
    con = duckdb.connect(str(harness.warehouse), read_only=True)
    try:
        notes = {r[0] for r in con.execute(f'SELECT note FROM bronze."{table[7:]}"').fetchall()}
    finally:
        con.close()
    assert set(HOSTILE_CELLS.values()) <= notes
    assert csv_bytes(["a"], [[1]]) == b"a\n1\n"


def test_upload_that_is_quarantined_is_a_control_failure(harness) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ControlFailed):
        harness.upload("empty.csv", b"", space_id=FINANCE)


def test_flags_apply_for_one_test_and_settings_hold_no_secret(harness_factory) -> None:  # type: ignore[no-untyped-def]
    from dms_api.settings import get_settings
    from dms_executor.cca.cascade import cascade_enabled

    h = harness_factory()
    settings = get_settings()
    assert settings.dms_demo_fallback is False
    assert settings.dms_ask_mode == "live"
    assert settings.database_url is None
    assert cascade_enabled() is False
    for key in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY", "DATABASE_URL"):
        assert key not in os.environ

    h.set_env(DMS_DEMO_FALLBACK="1", DMS_ASK_MODE="demo", DMS_CCA_CASCADE="1")
    settings = get_settings()
    assert settings.dms_demo_fallback is True
    assert settings.dms_ask_mode == "demo"
    assert cascade_enabled() is True


def test_flags_do_not_leak_into_the_next_test() -> None:
    from dms_api.settings import get_settings
    from dms_executor.cca.cascade import cascade_enabled

    assert os.environ.get("DMS_CCA_CASCADE", "0") != "1"
    assert cascade_enabled() is False
    assert get_settings().dms_ask_mode == "live"


# ------------------------------------------------------ gap / control mechanics


def test_control_failure_is_not_an_assertion_error() -> None:
    assert not issubclass(ControlFailed, AssertionError)
    with pytest.raises(ControlFailed, match="sibling did not pass"):
        control(False, "sibling did not pass")
    control(True, "never raised")


def test_assert_envelope_is_control_by_default_and_gap_on_request() -> None:
    bad = {"badge": "L0_CERTIFIED", "abstained": False, "text": "", "values": []}
    with pytest.raises(ControlFailed):
        assert_envelope(bad)
    with pytest.raises(AssertionError) as caught:
        assert_envelope(bad, as_gap=True)
    assert not isinstance(caught.value, ControlFailed)


def test_require_envelope_refuses_a_request_that_never_reached_the_answer_path() -> None:
    with pytest.raises(ControlFailed, match="404"):
        require_envelope(404, {"detail": "space_not_found"})


def test_gap_applies_marker_and_strict_assertion_only_xfail() -> None:
    @gap("R-12", "F5 compliance_gate", "#999", "GM-SELF")
    def body() -> None: ...

    marks = {m.name: m for m in body.pytestmark}
    assert marks["gate_gap"].kwargs == {
        "row": "R-12",
        "gate": "F5 compliance_gate",
        "ticket": "#999",
        "gap_id": "GM-SELF",
    }
    assert marks["xfail"].kwargs["strict"] is True
    assert marks["xfail"].kwargs["raises"] is AssertionError
    assert "GM-SELF" in marks["xfail"].kwargs["reason"]


_INNER = """
import sys
sys.path.insert(0, {harness_dir!r})
from _harness import control, gap


@gap("R-0", "G", "new", "INNER-1")
def test_assert_false_is_xfail():
    assert False, "the gap assertion"


@gap("R-0", "G", "new", "INNER-2")
def test_runtime_error_is_a_failure():
    raise RuntimeError("broken fixture")


@gap("R-0", "G", "new", "INNER-3")
def test_keyerror_is_a_failure():
    {{}}["abstained"]


@gap("R-0", "G", "new", "INNER-4")
def test_failing_control_is_a_failure():
    control(False, "sibling on the gated path did not pass")
    assert False, "never reached"


@gap("R-0", "G", "new", "INNER-5")
def test_a_gap_that_closed_is_loud():
    assert True
"""

#: The same mechanics on the real harness: the shape every gap test takes.
_INNER_REAL = """
from _harness import FINANCE, WAREHOUSE_OPS, assert_envelope, control, gap, require_envelope
from dms_executor.demo_pack import SPEND_BY_COUNTRY_Q


def _control_pack_answers(h):
    status, env = h.ask(SPEND_BY_COUNTRY_Q, space_id=FINANCE, session_id="ses_c")
    control(status == 200, f"control HTTP {{status}}")
    assert_envelope(env)
    control(env["badge"] == "L1_GOVERNED_METRIC" and env["rows"], env["text"])


@gap("R-0", "G", "new", "REAL-1")
def test_gap_assertion_fails_on_a_healthy_harness(harness):
    _control_pack_answers(harness)
    status, env = harness.ask(SPEND_BY_COUNTRY_Q, space_id=WAREHOUSE_OPS, session_id="ses_g")
    require_envelope(status, env)
    assert env["abstained"] is False, "pretend the gate is missing: Ops is answered"


@gap("R-0", "G", "new", "REAL-2")
def test_unknown_space_is_a_failure_not_an_xfail(harness):
    status, env = harness.ask(SPEND_BY_COUNTRY_Q, space_id="no-such-space", session_id="ses_g")
    require_envelope(status, env)
    assert env["abstained"] is False


@gap("R-0", "G", "new", "REAL-3")
def test_broken_control_is_a_failure(harness_factory):
    h = harness_factory(submit_ok=False)
    _control_pack_answers(h)
    assert h.cortex.sql_submits == []
"""


def _outcomes(report: Path) -> dict[str, tuple[str, str]]:
    outcomes: dict[str, tuple[str, str]] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        name = case.attrib["name"]
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        skipped = case.find("skipped")
        if bad is not None:
            outcomes[name] = ("FAILED", bad.attrib.get("message", "") + (bad.text or ""))
        elif skipped is not None:
            kind = "XFAIL" if skipped.attrib.get("type") == "pytest.xfail" else "SKIPPED"
            outcomes[name] = (kind, skipped.attrib.get("message", ""))
        else:
            outcomes[name] = ("PASSED", "")
    return outcomes


def _run_inner(
    tmp_path: Path, source: str, *, real_harness: bool
) -> tuple[dict[str, tuple[str, str]], str]:
    """Run pytest on a temporary inner test file; return (outcomes, output)."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "test_inner.py").write_text(source.format(harness_dir=str(HERE)), encoding="utf-8")
    report = tmp_path / "inner.xml"
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(inner),
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit2",
        f"--junitxml={report}",
        "-rxXf",
    ]
    if real_harness:
        # The repo's pythonpath config, the real conftest fixtures and harness.
        for name in ("conftest.py", "_harness.py"):
            (inner / name).write_text((HERE / name).read_text(encoding="utf-8"), encoding="utf-8")
        cmd += ["-c", str(REPO / "pyproject.toml"), f"--rootdir={REPO}", f"--confcutdir={inner}"]
        cwd = REPO
    else:
        cmd += ["-o", "markers=gate_gap: inner", f"--rootdir={inner}"]
        cwd = inner
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    return _outcomes(report), proc.stdout + proc.stderr


def test_gap_outcomes_by_running_pytest_on_an_inner_file(tmp_path: Path) -> None:
    """The proof: XFAIL only for the gap assertion; everything else is a FAILURE."""
    outcomes, output = _run_inner(tmp_path, _INNER, real_harness=False)
    got = {name: kind for name, (kind, _) in outcomes.items()}

    assert got == {
        "test_assert_false_is_xfail": "XFAIL",
        "test_runtime_error_is_a_failure": "FAILED",
        "test_keyerror_is_a_failure": "FAILED",
        "test_failing_control_is_a_failure": "FAILED",
        "test_a_gap_that_closed_is_loud": "FAILED",
    }, output
    assert "RuntimeError" in outcomes["test_runtime_error_is_a_failure"][1]
    assert "KeyError" in outcomes["test_keyerror_is_a_failure"][1]
    assert "ControlFailed" in outcomes["test_failing_control_is_a_failure"][1]
    assert "XPASS(strict)" in outcomes["test_a_gap_that_closed_is_loud"][1]
    assert "GATE GAP INNER-1" in outcomes["test_assert_false_is_xfail"][1]


def test_gap_shape_on_the_real_harness(tmp_path: Path) -> None:
    """control -> require_envelope -> gap assertion, with the real fixtures."""
    outcomes, output = _run_inner(tmp_path, _INNER_REAL, real_harness=True)
    got = {name: kind for name, (kind, _) in outcomes.items()}

    assert got == {
        "test_gap_assertion_fails_on_a_healthy_harness": "XFAIL",
        "test_unknown_space_is_a_failure_not_an_xfail": "FAILED",
        "test_broken_control_is_a_failure": "FAILED",
    }, output
    assert "HTTP 200" in outcomes["test_unknown_space_is_a_failure_not_an_xfail"][1]
    assert "ControlFailed" in outcomes["test_broken_control_is_a_failure"][1]
