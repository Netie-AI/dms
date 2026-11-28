"""UX-PLAIN-01: every reason code the server can emit has a UI sentence.

The copy table is apps/ui/src/lib/reasonCopy.ts. This test reads that file
and the executor sources. It does not score answers and it does not call /ask.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXEC = ROOT / "packages" / "executor"
COPY = ROOT / "apps" / "ui" / "src" / "lib" / "reasonCopy.ts"
BADGE = ROOT / "apps" / "ui" / "src" / "lib" / "badgeCopy.ts"
ENVELOPE = EXEC / "dms_executor" / "envelope.py"
PIPELINE = EXEC / "dms_executor" / "pipeline_failure.py"
GAPS = EXEC / "dms_executor" / "gen_path_refuse.py"

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_NAMED = {"GRANT_UNREADABLE", "JOIN_CONFIRM_CODE", "NO_MODEL_SQL"}
_REASON_KW = {"build_abstain", "abstain", "_abstain_env", "bronze_grant_abstain"}


def _assign_set(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call):
            value = value.args[0]
        if isinstance(value, ast.Set):
            return {elt.value for elt in value.elts if isinstance(elt, ast.Constant)}
    raise AssertionError(f"{name} not found in {path}")


def _add(found: set[str], code: object) -> None:
    if not isinstance(code, str):
        return
    head = code.split(":", 1)[0].strip()
    if _IDENT.fullmatch(head):
        found.add(head)


class _Scan(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = ""
        if name in {"_reconfirm", "abstain", "_abstain_env"} and node.args:
            _add(self.found, _const(node.args[0]))
        if name == "_reconfirm" and len(node.args) >= 2:
            _add(self.found, _const(node.args[1]))
        for kw in node.keywords:
            if kw.arg in {"abstain_reason", "reason_code", "ticket_reason"}:
                _add(self.found, _const(kw.value))
            if kw.arg == "reason" and name in _REASON_KW:
                _add(self.found, _const(kw.value))
                joined = name == "bronze_grant_abstain" and isinstance(kw.value, ast.JoinedStr)
                if joined and kw.value.values:
                    first = kw.value.values[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        _add(self.found, first.value.split(":", 1)[0])
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value in {"reason_code", "abstain_reason"}:
                _add(self.found, _const(value))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in _NAMED:
                _add(self.found, _const(node.value))
        self.generic_visit(node)


def _const(node: ast.expr) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _record_keys(source: str, export_name: str, end_marker: str) -> dict[str, str]:
    body = source.split(f"export const {export_name}", 1)[1].split(end_marker, 1)[0]
    pairs: dict[str, str] = {}
    key: str | None = None
    for line in body.splitlines():
        one = re.match(r'  ([A-Za-z0-9_]+): "(.*)"', line)
        if one:
            pairs[one.group(1)] = one.group(2)
            key = None
            continue
        start = re.match(r"  ([A-Za-z0-9_]+):\s*$", line)
        if start:
            key = start.group(1)
            continue
        if key and '"' in line:
            parts = re.findall(r'"([^"]*)"', line)
            if parts:
                pairs[key] = "".join(parts)
                key = None
    return pairs


def emitted_reason_codes() -> set[str]:
    found = _assign_set(PIPELINE, "_CODES") | _assign_set(GAPS, "GAP_REASONS")
    scan = _Scan()
    for path in EXEC.rglob("*.py"):
        scan.visit(ast.parse(path.read_text(encoding="utf-8")))
    found |= scan.found
    validate = ast.parse((EXEC / "dms_executor" / "def_validate.py").read_text(encoding="utf-8"))

    class _Returns(ast.NodeVisitor):
        def visit_Return(self, node: ast.Return) -> None:
            value = node.value
            if isinstance(value, ast.Tuple) and value.elts:
                _add(found, _const(value.elts[0]))
            self.generic_visit(node)

    _Returns().visit(validate)
    if "currency_mismatch:" in GAPS.read_text(encoding="utf-8"):
        found.add("currency_mismatch")
    if 'else "unspecified"' in PIPELINE.read_text(encoding="utf-8"):
        found.add("unspecified")
    return found


def test_every_emitted_reason_has_ui_copy() -> None:
    copy = _record_keys(
        COPY.read_text(encoding="utf-8"),
        "REASON_COPY",
        "export const BADGE_SENTENCE",
    )
    missing = sorted(emitted_reason_codes() - set(copy))
    assert not missing, missing
    for code, sentence in copy.items():
        assert sentence.strip(), code
        assert code not in sentence, code
        assert "—" not in sentence, code
        assert "No plain-language note" not in sentence, code


def test_every_badge_has_a_sentence_and_a_chip() -> None:
    allowed = _assign_set(ENVELOPE, "ALLOWED_BADGES")
    source = COPY.read_text(encoding="utf-8")
    sentences = _record_keys(source, "BADGE_SENTENCE", "const BADGE_PHRASE")
    chips = set(re.findall(r"^  ([A-Z0-9_]+): \{", BADGE.read_text(encoding="utf-8"), re.M))
    assert allowed <= set(sentences), sorted(allowed - set(sentences))
    assert allowed <= chips, sorted(allowed - chips)
    for kind, sentence in sentences.items():
        assert kind not in sentence, kind
        assert "badge=" not in sentence
