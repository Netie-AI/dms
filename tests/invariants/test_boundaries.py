"""Boundary invariants — AST / import guards.

Protected path: any PR that touches this file or ``.importlinter`` must include
``INVARIANT-CHANGE: <reason>`` in the commit body (enforced by CI).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PYTHON_ROOTS = [
    ROOT / "apps" / "api",
    ROOT / "packages",
]

SKIP_DIR_NAMES = {
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "archive",
    ".git",
}

EXCEL_WRITE_PATTERNS = (
    re.compile(r"\bto_excel\b"),
    re.compile(r"openpyxl\.Workbook"),
    re.compile(r"\.save\s*\("),  # refined in visitor for Workbook.save
    re.compile(r"\bxlsxwriter\b"),
)

MUTATING_METHODS = {"post", "put", "patch", "delete"}

#: Every HTTP method a route can be declared with. ``MUTATING_METHODS`` above is a
#: subset, and using it as the only classifier is what made this file green on the
#: surface that leaked: the gate inspected no GET at all, so it could not fail on one
#: however many customer rows the route returned (A-0007, dms#73).
ROUTE_METHODS = MUTATING_METHODS | {"get", "head", "options"}

#: Callees that reach customer data - warehouse rows, bronze uploads, document chunks,
#: or a Postgres connection carrying tenant context. A route is classified by what its
#: body REACHES, not by its verb. ``set_tenant_context`` stands in for the control
#: plane generally: it is called on every Postgres path in ``apps/api`` precisely
#: because RLS needs it, so it is the reliable marker for "this route reads the
#: control plane".
DATA_REVEALING_CALLEES = frozenset(
    {
        # warehouse / bronze reads
        "warehouse_preview",
        "bronze_preview",
        "warehouse_tables",
        "bronze_list",
        "library_tree",
        # document chunks
        "search_document_chunks",
        "list_document_chunks",
        # filesystem
        "reveal_origin_uri",
        # control plane
        "set_tenant_context",
        # writes that also return data
        "batch_ingest",
        "bronze_ingest",
        "sql_source_ingest",
        "pipeline_run",
        "gold_sign_metric",
    }
)


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_ROOTS:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    return files


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_under_executor(path: Path) -> bool:
    try:
        path.relative_to(ROOT / "packages" / "executor")
        return True
    except ValueError:
        return False


class _BoundaryVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.duckdb_execute: list[int] = []
        self.excel_writes: list[tuple[int, str]] = []
        self.cortexos_imports: list[int] = []
        self.mutation_routes: list[tuple[int, str, bool]] = []  # lineno, decorator, has_gate
        #: A-0007 (#73). GET handlers that take an OPTIONAL space_id and guard
        #: their scope check behind it: (lineno, function name).
        self.skippable_scope_routes: list[tuple[int, str]] = []
        #: lineno, method, name, has_gate, sorted callees reached
        self.data_routes: list[tuple[int, str, str, bool, tuple[str, ...]]] = []
        self._current_fn_has_gate = False
        self._current_fn_is_mutation = False
        self._current_fn_lineno = 0
        self._current_fn_deco = ""
        self._current_fn_method = ""
        self._current_fn_name = ""
        self._current_fn_reaches: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "CortexOS" or alias.name.startswith("CortexOS."):
                self.cortexos_imports.append(node.lineno)
            if alias.name == "xlsxwriter" or alias.name.startswith("xlsxwriter."):
                self.excel_writes.append((node.lineno, f"import {alias.name}"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod == "CortexOS" or mod.startswith("CortexOS."):
            self.cortexos_imports.append(node.lineno)
        if mod == "xlsxwriter" or mod.startswith("xlsxwriter."):
            self.excel_writes.append((node.lineno, f"from {mod}"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # duckdb.execute(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "execute":
            # duckdb.execute or <conn>.execute — flag duckdb module attr or name 'execute' on duckdb
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "duckdb":
                self.duckdb_execute.append(node.lineno)
            elif isinstance(base, ast.Attribute) and base.attr == "duckdb":
                self.duckdb_execute.append(node.lineno)

        # to_excel(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "to_excel":
            self.excel_writes.append((node.lineno, "to_excel"))

        # openpyxl.Workbook(...).save or Workbook.save
        if isinstance(node.func, ast.Attribute) and node.func.attr == "save":
            self.excel_writes.append((node.lineno, "Workbook.save/save"))

        # compliance_gate(...)
        if isinstance(node.func, ast.Name) and node.func.id == "compliance_gate":
            self._current_fn_has_gate = True
        if isinstance(node.func, ast.Attribute) and node.func.attr == "compliance_gate":
            self._current_fn_has_gate = True

        # Anything this route reaches that returns customer data.
        callee = None
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr
        if callee in DATA_REVEALING_CALLEES:
            self._current_fn_reaches.add(callee)

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_fn(node)

    def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        deco_method = self._mutation_decorator(node)
        route_method = self._any_route_method(node)
        prev_gate = self._current_fn_has_gate
        prev_mut = self._current_fn_is_mutation
        prev_lineno = self._current_fn_lineno
        prev_deco = self._current_fn_deco
        prev_method = self._current_fn_method
        prev_name = self._current_fn_name
        prev_reaches = self._current_fn_reaches

        self._current_fn_has_gate = False
        self._current_fn_is_mutation = deco_method is not None
        self._current_fn_lineno = node.lineno
        self._current_fn_deco = deco_method or ""
        self._current_fn_method = route_method or ""
        self._current_fn_name = node.name
        self._current_fn_reaches = set()

        self.generic_visit(node)

        if self._current_fn_is_mutation:
            self.mutation_routes.append(
                (self._current_fn_lineno, self._current_fn_deco, self._current_fn_has_gate)
            )
        if _is_get_route(node) and _has_skippable_scope_check(node):
            self.skippable_scope_routes.append((node.lineno, node.name))
        if route_method and self._current_fn_reaches:
            self.data_routes.append(
                (
                    self._current_fn_lineno,
                    self._current_fn_method,
                    self._current_fn_name,
                    self._current_fn_has_gate,
                    tuple(sorted(self._current_fn_reaches)),
                )
            )

        self._current_fn_has_gate = prev_gate
        self._current_fn_is_mutation = prev_mut
        self._current_fn_lineno = prev_lineno
        self._current_fn_deco = prev_deco
        self._current_fn_method = prev_method
        self._current_fn_name = prev_name
        self._current_fn_reaches = prev_reaches

    def _mutation_decorator(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        for deco in node.decorator_list:
            method = self._router_method(deco)
            if method in MUTATING_METHODS:
                return method
        return None

    def _any_route_method(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        """The route's HTTP method whatever it is - GET included.

        ``_mutation_decorator`` deliberately stays narrow because the mutation gate
        means something different (a write must fail closed). This one classifies the
        route so the *data-revealing* check can look at every verb.
        """
        for deco in node.decorator_list:
            func = deco.func if isinstance(deco, ast.Call) else deco
            if isinstance(func, ast.Attribute) and func.attr.lower() in ROUTE_METHODS:
                return func.attr.lower()
        return None

    def _router_method(self, deco: ast.AST) -> str | None:
        # @router.post(...) / @app.put(...)
        if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
            if deco.func.attr.lower() in MUTATING_METHODS:
                return deco.func.attr.lower()
        if isinstance(deco, ast.Attribute) and deco.attr.lower() in MUTATING_METHODS:
            return deco.attr.lower()
        return None


def _is_get_route(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for deco in node.decorator_list:
        if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
            if deco.func.attr.lower() == "get":
                return True
        if isinstance(deco, ast.Attribute) and deco.attr.lower() == "get":
            return True
    return False


def _space_id_is_optional(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the handler accepts ``space_id`` and the caller may omit it.

    Required (``Query(...)``) is fine - a caller cannot skip the check by
    leaving it out, because FastAPI answers 422 instead.
    """
    args = node.args
    positional = args.posonlyargs + args.args
    pos_defaults = dict(zip([a.arg for a in positional[len(positional) - len(args.defaults):]],
                            args.defaults))
    kw_defaults = {a.arg: d for a, d in zip(args.kwonlyargs, args.kw_defaults)}
    default = pos_defaults.get("space_id", kw_defaults.get("space_id"))
    if default is None:
        # Either no space_id at all, or it is required with no default.
        return False
    if isinstance(default, ast.Constant) and default.value is None:
        return True
    # Query(None) / Query(default=None) is optional; Query(...) is required.
    if isinstance(default, ast.Call):
        for a in default.args:
            if isinstance(a, ast.Constant) and a.value is None:
                return True
            if isinstance(a, ast.Constant) and a.value is Ellipsis:
                return False
        for kw in default.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                return kw.value.value is None
    return False


def _guards_on_space_id(test: ast.AST) -> bool:
    for sub in ast.walk(test):
        if isinstance(sub, ast.Name) and sub.id == "space_id":
            return True
    return False


def _has_skippable_scope_check(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when ``space_id``'s only job is deciding whether scoping happens.

    The A-0007 shape. Verb is the wrong classifier for a data-revealing route,
    and so is "does it call compliance_gate": the leak was not an ungated route,
    it was a route whose *scope* check the caller could switch off by leaving a
    query parameter blank.

    The signal is what ``space_id`` is used FOR. If every reference to it sits
    inside an ``if space_id:`` - as the test, or within the block that test
    guards - then omitting it skips the scoping entirely. If it is also fed into
    scope computation outside that branch, the scoping happens either way and
    the branch only chooses *which* scope, which is what the fixed routes do.

    My first attempt at this asked "is there a raise outside the guard", and it
    passed on the known-leaky code: the unrelated ``except ValueError -> 404``
    re-raise counted as a refusal outside the branch. A detector that green-lit
    the very routes it was written for is the same R-0007 failure as the
    invariant it replaces, so it is worth naming rather than quietly rewriting.
    """
    if not _space_id_is_optional(node):
        return False

    guards = [
        stmt
        for stmt in ast.walk(node)
        if isinstance(stmt, ast.If) and _guards_on_space_id(stmt.test)
    ]
    if not guards:
        return False

    guarded_nodes: set[int] = set()
    for g in guards:
        for n in ast.walk(g):
            guarded_nodes.add(id(n))

    for ref in ast.walk(node):
        if isinstance(ref, ast.Name) and ref.id == "space_id" and id(ref) not in guarded_nodes:
            return False  # used outside the branch - scoping is not optional
    return True


def _scan(path: Path) -> _BoundaryVisitor:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    visitor = _BoundaryVisitor(path)
    visitor.visit(tree)

    # Text fallback for xlsxwriter / to_excel if AST missed string forms
    for i, line in enumerate(src.splitlines(), start=1):
        if "xlsxwriter" in line and "import" in line:
            if not any(ln == i for ln, _ in visitor.excel_writes):
                visitor.excel_writes.append((i, "xlsxwriter"))
        if re.search(r"\bto_excel\b", line):
            if not any(ln == i for ln, _ in visitor.excel_writes):
                visitor.excel_writes.append((i, "to_excel"))
        if "openpyxl.Workbook" in line:
            if not any(ln == i for ln, _ in visitor.excel_writes):
                visitor.excel_writes.append((i, "openpyxl.Workbook"))
    return visitor


@pytest.fixture(scope="module")
def scans() -> dict[str, _BoundaryVisitor]:
    return {_rel(p): _scan(p) for p in _iter_py_files()}


def test_no_duckdb_execute_outside_executor(scans: dict[str, _BoundaryVisitor]) -> None:
    offenders: list[str] = []
    for rel, v in scans.items():
        path = ROOT / rel
        if _is_under_executor(path):
            continue
        for lineno in v.duckdb_execute:
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, "duckdb.execute only allowed in packages/executor:\n" + "\n".join(
        offenders
    )


def test_no_excel_write_anywhere(scans: dict[str, _BoundaryVisitor]) -> None:
    """Excel is source-only — no to_excel / openpyxl save / xlsxwriter."""
    offenders: list[str] = []
    for rel, v in scans.items():
        for lineno, kind in v.excel_writes:
            # Allow compliance/docs strings? No — fail any code hit.
            # Narrow: skip if line is only a comment about the ban
            line = (ROOT / rel).read_text(encoding="utf-8").splitlines()[lineno - 1].strip()
            if line.startswith("#"):
                continue
            # `.save(` is noisy — only flag when openpyxl/Workbook nearby in file or same line
            if kind == "Workbook.save/save":
                text = (ROOT / rel).read_text(encoding="utf-8")
                if "openpyxl" not in text and "Workbook" not in text and "xlsx" not in text.lower():
                    continue
            offenders.append(f"{rel}:{lineno} ({kind})")
    assert not offenders, "Excel write APIs forbidden:\n" + "\n".join(offenders)


def test_mutation_routes_call_compliance_gate(scans: dict[str, _BoundaryVisitor]) -> None:
    offenders: list[str] = []
    for rel, v in scans.items():
        if not rel.startswith("apps/api/"):
            continue
        for lineno, method, has_gate in v.mutation_routes:
            if not has_gate:
                offenders.append(f"{rel}:{lineno} @{method} missing compliance_gate()")
    assert not offenders, "Mutation routes must call compliance_gate:\n" + "\n".join(offenders)


def test_data_revealing_routes_call_compliance_gate(scans: dict[str, _BoundaryVisitor]) -> None:
    """Any route that REACHES customer data must gate, whatever its HTTP method.

    dms#73. ``test_mutation_routes_call_compliance_gate`` above classifies by verb,
    which is the right classifier for the question it asks (a write must fail closed)
    and the wrong one for this question. Verb-only meant the suite inspected no GET,
    so it was green while ``GET /v1/library/warehouse/alerts/preview`` returned rows of
    a table no Space grants (A-0007, dms#72).

    There is deliberately **no allowlist**. An allowlist here would recreate the exact
    failure this test exists to prevent - a green result standing in for a surface
    nobody looked at. If a route legitimately needs no gate, it must not reach one of
    ``DATA_REVEALING_CALLEES``; if it does reach one, it gates.
    """
    offenders: list[str] = []
    for rel, v in scans.items():
        if not rel.startswith("apps/api/"):
            continue
        for lineno, method, name, has_gate, reached in v.data_routes:
            if not has_gate:
                offenders.append(
                    f"{rel}:{lineno} @{method} {name}() reaches {', '.join(reached)} "
                    f"without compliance_gate"
                )
    assert not offenders, (
        "Routes that reach customer data must call compliance_gate "
        "(classified by what the body reaches, not by HTTP method):\n" + "\n".join(offenders)
    )


def test_the_data_revealing_check_actually_inspects_get_routes(
    scans: dict[str, _BoundaryVisitor],
) -> None:
    """Guard the guard: prove the check has GETs in scope at all.

    Without this, deleting ``"get"`` from ``ROUTE_METHODS`` would leave
    ``test_data_revealing_routes_call_compliance_gate`` passing while inspecting
    nothing - which is precisely the defect being fixed, reintroduced silently. A gate
    whose scope can empty without failing is not evidence (R-0007).
    """
    get_routes = [
        f"{rel}:{lineno} {name}"
        for rel, v in scans.items()
        if rel.startswith("apps/api/")
        for lineno, method, name, _gate, _reached in v.data_routes
        if method == "get"
    ]
    assert len(get_routes) >= 8, (
        "the data-revealing check is inspecting almost no GET routes, so its green "
        f"result means nothing. Found {len(get_routes)}: {get_routes}"
    )


def test_no_cortexos_imports(scans: dict[str, _BoundaryVisitor]) -> None:
    offenders: list[str] = []
    for rel, v in scans.items():
        for lineno in v.cortexos_imports:
            offenders.append(f"{rel}:{lineno}")
    assert not offenders, "CortexOS import forbidden — use cortex_client HTTP:\n" + "\n".join(
        offenders
    )


def test_no_shadow_openapi_without_sha256() -> None:
    """Vendored OpenAPI must carry a sibling .sha256 matching Cortex pin."""
    for name in ("openapi-1.1.0.json", "openapi-1.0.0.json"):
        path = ROOT / "contract" / name
        sha = ROOT / "contract" / f"{name}.sha256"
        if path.is_file():
            assert sha.is_file(), f"{name} exists without sibling .sha256"
            import hashlib

            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = sha.read_text(encoding="utf-8").split()[0]
            assert actual == expected, f"{name} sha256 drift"


_GATE_NAME_RE = re.compile(
    r"(^|_)(gate|policy|authorize|check_compliance)(_|$)",
    re.IGNORECASE,
)


def test_no_local_gate_policy_functions() -> None:
    """One gate, in Cortex — DMS may only define call-throughs under cortex_client."""
    allowed_root = ROOT / "packages" / "cortex_client"
    offenders: list[str] = []
    for path in _iter_py_files():
        try:
            path.relative_to(allowed_root)
            continue
        except ValueError:
            pass
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _GATE_NAME_RE.search(node.name):
                    offenders.append(f"{_rel(path)}:{node.lineno} def {node.name}")
    assert not offenders, (
        "gate/policy/authorize/check_compliance defs only allowed in "
        "packages/cortex_client:\n" + "\n".join(offenders)
    )


def test_agent_contract_docs_present() -> None:
    for rel in ("CLAUDE.md", ".cursorrules", "AGENTS.md", ".importlinter"):
        assert (ROOT / rel).is_file(), f"missing {rel}"


def test_data_revealing_gets_cannot_skip_their_scope_check(
    scans: dict[str, _BoundaryVisitor],
) -> None:
    """A-0007 (#73). The boundary invariant used to inspect no GET at all.

    ``MUTATING_METHODS`` was the only classifier, so ``test_mutation_routes_
    call_compliance_gate`` iterated POST/PUT/PATCH/DELETE and nothing else. The
    suite was green while ``GET /v1/library/warehouse/alerts/preview`` with no
    ``space_id`` returned rows of a table no Space grants. That is R-0007
    exactly: green from a check that never ran.

    Verb is the wrong classifier. A GET that returns warehouse rows reveals as
    much as any mutation writes, and the failure here was not an ungated route -
    it was a route whose *scope* check the caller could switch off by leaving a
    query parameter blank. So this asserts on the shape: an optional
    ``space_id`` whose truthiness decides whether the refusal is reachable.

    Scoping unconditionally and branching on ``space_id`` only to choose *which*
    scope is fine, and is what the fixed routes do.
    """
    offenders: list[str] = []
    for rel, v in scans.items():
        if not rel.startswith("apps/api/"):
            continue
        for lineno, name in v.skippable_scope_routes:
            offenders.append(
                f"{rel}:{lineno} {name}() - scope check is inside `if space_id:`, "
                "so omitting the parameter skips it"
            )
    assert not offenders, (
        "A data-revealing GET must not let the caller switch off its own scope check "
        "by omitting space_id:\n" + "\n".join(offenders)
    )
