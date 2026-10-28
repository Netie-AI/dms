"""One abstain builder. The structural scan covers the served package."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import dms_executor.pipeline_failure as tickets
import pytest
from dms_executor.abstain import backstop_missing_ticket, build_abstain
from test_c_loop_b import _ask, _assert_abstain, _loop, _names
from test_pipeline_failure_01 import _groups, _records, _watch

_PACKAGE = Path(__file__).resolve().parents[1] / "packages" / "executor" / "dms_executor"
_BUILDER = "build_abstain"

# The shape of the three exits on 28f463c0. The scanner names them from the
# calls, not from a list of reasons the product must ticket.
_OLD_BUDGET = """
def _abstain(reason):
    return build_answer_envelope(badge="ABSTAIN", abstained=True)

def maybe(payload):
    budget_stop = insights_budget_stop(payload)
    if budget_stop:
        return _abstain(budget_stop)
"""

_OLD_AS_OF = """
def reserved_as_of_abstain():
    env = build_answer_envelope(badge="ABSTAIN", abstained=True)
    env["abstain_reason"] = RESERVED_PARAM_AS_OF
    return env
"""

_SECOND_MODULE = """
def side():
    return {"badge": "ABSTAIN", "abstained": True}
"""


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _kw_is_abstain(kw: ast.keyword) -> bool:
    if kw.arg == "badge":
        return isinstance(kw.value, ast.Constant) and kw.value.value == "ABSTAIN"
    if kw.arg == "abstained":
        return isinstance(kw.value, ast.Constant) and kw.value.value is True
    if kw.arg == "abstain_reason":
        return True
    return False


def _dict_is_abstain(node: ast.AST) -> bool:
    if not isinstance(node, ast.Dict):
        return False
    for key, val in zip(node.keys, node.values, strict=False):
        if key is None and _dict_is_abstain(val):
            return True
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if key.value == "badge" and isinstance(val, ast.Constant) and val.value == "ABSTAIN":
            return True
        if key.value == "abstained" and isinstance(val, ast.Constant) and val.value is True:
            return True
        if key.value == "abstain_reason":
            return True
    return False


def _subscript_key(node: ast.Subscript) -> str | None:
    sl = node.slice
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def _assigned_calls(fn: ast.AST) -> dict[str, str]:
    found: dict[str, str] = {}
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Assign) or len(sub.targets) != 1:
            continue
        target = sub.targets[0]
        if isinstance(target, ast.Name) and isinstance(sub.value, ast.Call):
            found[target.id] = _call_name(sub.value)
    return found


class _Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.violations: list[tuple[int, str]] = []

    def _inside(self) -> bool:
        return _BUILDER in self.stack

    def _note(self, lineno: int, kind: str) -> None:
        if self._inside():
            return
        self.violations.append((lineno, kind))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:
        called = _call_name(node)
        for kw in node.keywords:
            if kw.arg is None and _dict_is_abstain(kw.value):
                self._note(node.lineno, "spread")
            elif called != _BUILDER and _kw_is_abstain(kw):
                self._note(node.lineno, "call")
        if _call_name(node) == "build_answer_envelope":
            for kw in node.keywords:
                if kw.arg != "abstained":
                    continue
                false = isinstance(kw.value, ast.Constant) and kw.value.value is False
                if not false:
                    self._note(node.lineno, "bypass")
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if _dict_is_abstain(node):
            self._note(node.lineno, "dict")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = _subscript_key(target)
            badge = (
                key == "badge"
                and isinstance(node.value, ast.Constant)
                and node.value.value == "ABSTAIN"
            )
            if badge:
                self._note(node.lineno, "store")
            elif (
                key == "abstained"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                self._note(node.lineno, "store")
            elif key == "abstain_reason":
                self._note(node.lineno, "store")
        self.generic_visit(node)


def violations_in_source(src: str) -> list[tuple[int, str]]:
    visitor = _Visitor()
    visitor.visit(ast.parse(src))
    return visitor.violations


def _constructors(tree: ast.AST) -> set[str]:
    found: set[str] = set()

    class _Fns(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.generic_visit(node)
            if node.name == _BUILDER:
                return
            inner = _Visitor()
            inner.visit(node)
            if inner.violations:
                found.add(node.name)

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    _Fns().visit(tree)
    return found


def exit_names(src: str) -> set[str]:
    tree = ast.parse(src)
    if not violations_in_source(src):
        return set()
    constructors = _constructors(tree)
    names: set[str] = set()
    if any("as_of" in name for name in constructors):
        names.add("$as_of")

    class _Calls(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            assigned = _assigned_calls(node)
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                called = _call_name(sub)
                if called not in constructors and "as_of" not in called:
                    continue
                if "as_of" in called:
                    names.add("$as_of")
                args = list(sub.args)
                for kw in sub.keywords:
                    if kw.arg is not None:
                        args.append(kw.value)
                for arg in args:
                    if isinstance(arg, ast.Name) and assigned.get(arg.id) == "insights_budget_stop":
                        names.add("timeout")
                        names.add("call-cap")
            self.generic_visit(node)

    _Calls().visit(tree)
    return names


def scan_package(root: Path) -> str:
    lines: list[str] = []
    exits: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            tree_ok = True
            violations = violations_in_source(src)
        except SyntaxError as exc:
            tree_ok = False
            violations = [(exc.lineno or 0, "syntax")]
        rel = path.relative_to(root)
        for lineno, kind in violations:
            lines.append(f"{rel}:{lineno}: {kind}")
        if tree_ok:
            exits |= exit_names(src)
    if not lines:
        return ""
    named = ", ".join(sorted(exits))
    head = f"abstain constructed outside build_abstain ({len(lines)})"
    if named:
        head += f"\nexits: {named}"
    return head + "\n" + "\n".join(lines)


def test_served_package_abstains_only_through_the_builder() -> None:
    report = scan_package(_PACKAGE)
    assert report == "", report


def test_inline_dict_in_a_second_module_is_red() -> None:
    found = violations_in_source(_SECOND_MODULE)
    assert found, "an inline dict abstain in another module must fail the scan"


def test_old_budget_and_as_of_shapes_are_named() -> None:
    names = exit_names(_OLD_BUDGET) | exit_names(_OLD_AS_OF)
    assert "timeout" in names
    assert "call-cap" in names
    assert "$as_of" in names


@pytest.fixture(autouse=True)
def _clear_groups() -> None:
    tickets._reset_pipeline_failures()


def test_timeout_call_cap_and_as_of_each_write_one_ticket(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _loop(monkeypatch)
    _watch(caplog)
    question = "how many florbs are in the warehouse"
    cases = (
        (
            "insights_timeout:generate",
            "insights_budget",
            {"insights_fail": "insights_timeout:generate", "query_sql": "SELECT 1"},
        ),
        (
            "insights_call_cap",
            "insights_budget",
            {"insights_fail": "insights_call_cap:2", "query_sql": "SELECT 1"},
        ),
        (
            "reserved_param:as_of",
            "extract_loop",
            {"query_sql": "SELECT $as_of AS day"},
        ),
    )
    for reason, stage, payload in cases:
        caplog.clear()
        tickets._reset_pipeline_failures()

        def compute(_ctx: dict[str, Any], payload: dict[str, Any] = payload) -> dict[str, Any]:
            return _names(**payload)

        env = _assert_abstain(_ask(tmp_path, question, compute))
        grouped = _groups(_records(caplog))
        assert len(grouped) == 1, reason
        ticket = next(iter(grouped.values()))
        assert ticket["reason"] == reason, ticket
        assert ticket["stage"] == stage, ticket
        assert ticket["count"] == 1
        assert env["ticket_id"] == ticket["ticket_id"]
        assert question not in caplog.text


def test_backstop_tickets_a_dynamic_abstain_and_a_raise_does_not_mutate(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DMS_CLOOP_B", "1")
    _watch(caplog)
    env = {
        "badge": "ABSTAIN",
        "abstained": True,
        "audit_id": "aud_dyn",
        "route": "generated",
        "abstain_reason": "no_sql",
        "rows": [],
        "values": [],
    }
    frozen = dict(env)
    backstop_missing_ticket(env, question="dynamic bypass")
    assert env == frozen
    grouped = _groups(_records(caplog))
    assert len(grouped) == 1
    ticket = next(iter(grouped.values()))
    assert ticket["reason"] == "ticket_missing:no_sql"
    assert ticket["stage"] == "generated"
    assert "dynamic bypass" not in caplog.text

    caplog.clear()
    tickets._reset_pipeline_failures()

    def _boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("backstop down")

    monkeypatch.setattr(tickets, "log_pipeline_failure_ticket", _boom)
    again = dict(frozen)
    backstop_missing_ticket(again, question="dynamic bypass")
    assert again == frozen
    assert any(
        rec.levelno == logging.WARNING
        and rec.getMessage() == "pipeline_failure ticket was not written"
        for rec in caplog.records
    )


def test_backstop_is_off_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    _watch(caplog)
    env = {"badge": "ABSTAIN", "abstained": True, "route": "generated", "abstain_reason": "no_sql"}
    frozen = dict(env)
    backstop_missing_ticket(env, question="off")
    assert env == frozen
    assert _records(caplog) == []


def test_builder_is_importable_and_flag_off_adds_no_ticket_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("DMS_CLOOP_B", raising=False)
    _watch(caplog)
    env = build_abstain(
        reason="no_sql",
        question="off",
        stage="generative",
        answer_id="ans_builder_off",
        text="I cannot certify that.",
        rows=[],
        values=[],
        sql_used=None,
    )
    assert env["badge"] == "ABSTAIN"
    assert env["abstained"] is True
    assert "ticket_id" not in env
    assert _records(caplog) == []
