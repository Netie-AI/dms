"""List question-text rule sites under packages/ and apps/.

A site is a regex call, or an ``in`` / ``not in`` test, whose operand traces
to a question argument. ``scripts/verify/word_rule_allowlist.txt`` is the
sites that exist today. That file may only shrink: a new key fails
``--check``. Deleting a site means deleting its line. Line numbers may be
replaced when a statement moves. The ceiling file is the day-1 key set and
does not grow.

Prints ``file:line`` and a count. Does not read held-out packs.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "packages", ROOT / "apps")
ALLOWLIST_PATH = Path(__file__).with_name("word_rule_allowlist.txt")
CEILING_PATH = Path(__file__).with_name("word_rule_allowlist.ceiling")

_QUESTION_PARAMS = frozenset({"question", "q", "qn"})
_STRING_METHODS = frozenset(
    {"lower", "upper", "casefold", "strip", "lstrip", "rstrip", "replace", "split"}
)
_WRAPS = frozenset({"_norm", "normalize_ask_question", "str", "question_tokens"})
_REGEX_ON_RECEIVER = frozenset({"search", "match", "fullmatch", "findall", "finditer"})
_RE_FUNCS = _REGEX_ON_RECEIVER | {"sub", "split"}
_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


@dataclass(frozen=True)
class Site:
    path: str
    line: int
    kind: str
    qual: str
    snippet: str
    ordinal: int = 1

    @property
    def key(self) -> str:
        return f"{self.path}\t{self.qual}\t{self.kind}\t{self.snippet}\t#{self.ordinal}"

    def display(self) -> str:
        return f"{self.path}:{self.line}\t{self.kind}\t{self.qual}\t{self.snippet}\t#{self.ordinal}"


def scan_roots(roots: tuple[Path, ...] = SCAN_ROOTS) -> list[Site]:
    sites: list[Site] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            sites.extend(scan_file(path, rel=_rel(path)))
    return sites


def scan_file(path: Path, *, rel: str | None = None) -> list[Site]:
    source = path.read_text(encoding="utf-8")
    return scan_source(source, rel or _rel(path))


def scan_source(source: str, rel: str) -> list[Site]:
    tree = ast.parse(source)
    sites = _scan_tree(tree, rel, source)
    sites.extend(_module_sites(tree, rel, source))
    return _with_ordinals(sites)


def allowlist_keys(text: str) -> dict[str, Site]:
    """Map key to the site recorded on that line. Blank and ``#`` lines skip."""
    found: dict[str, Site] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        site = _parse_display(line)
        found[site.key] = site
    return found


def ceiling_keys(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def violations(
    sites: list[Site],
    *,
    allow_text: str,
    ceiling_text: str,
) -> list[str]:
    """New keys, stale allowlist lines, and a ceiling that gained a key."""
    allow = allowlist_keys(allow_text)
    ceiling = ceiling_keys(ceiling_text)
    found = {site.key: site for site in sites}
    messages: list[str] = []
    for key in sorted(set(allow) - ceiling):
        messages.append(f"allowlist key is not in the day-1 ceiling: {key}")
    for key in sorted(set(found) - set(allow)):
        site = found[key]
        messages.append(f"new question-text rule (not on the allowlist): {site.display()}")
    for key in sorted(set(allow) - set(found)):
        messages.append(f"allowlist line is gone from the code; remove it: {allow[key].display()}")
    for key in sorted(set(found) & set(allow)):
        if found[key].line != allow[key].line:
            messages.append(
                "line moved; replace the number, do not add a key: "
                f"allowlist {allow[key].display()} now {found[key].display()}"
            )
    return messages


def render_allowlist(sites: list[Site]) -> str:
    header = (
        "# Question-text rule sites. This file may only shrink.\n"
        "# Do not add a key. Remove a line when that site is deleted.\n"
        "# A moved statement may replace its line number. Columns: "
        "path:line, kind, qualname, snippet.\n"
    )
    body = "\n".join(site.display() for site in sorted(sites, key=lambda item: item.key))
    return header + body + ("\n" if body else "")


def render_ceiling(sites: list[Site]) -> str:
    keys = sorted({site.key for site in sites})
    return "\n".join(keys) + ("\n" if keys else "")


def ceiling_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _parse_display(line: str) -> Site:
    path_line, kind, qual, snippet, ordinal = line.split("\t")
    path, _, lineno = path_line.rpartition(":")
    number = ordinal[1:] if ordinal.startswith("#") else ordinal
    return Site(path, int(lineno), kind, qual, snippet, int(number))


def _with_ordinals(sites: list[Site]) -> list[Site]:
    """Same statement twice in one function stays two keys, in source order."""
    counts: dict[str, int] = {}
    out: list[Site] = []
    for site in sites:
        base = f"{site.path}\t{site.qual}\t{site.kind}\t{site.snippet}"
        counts[base] = counts.get(base, 0) + 1
        out.append(Site(site.path, site.line, site.kind, site.qual, site.snippet, counts[base]))
    return out


def _module_sites(tree: ast.AST, rel: str, source: str) -> list[Site]:
    """A temp module can test ``'revenue' in question`` with no function."""
    if not isinstance(tree, ast.Module):
        return []
    body = [
        node
        for node in tree.body
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    if not body:
        return []
    args = ast.arguments(
        posonlyargs=[],
        args=[ast.arg(arg=name) for name in ("question", "q", "qn")],
        kwonlyargs=[],
        kw_defaults=[],
        defaults=[],
    )
    synthetic = ast.FunctionDef(
        name="<module>",
        args=args,
        body=body,
        decorator_list=[],
    )
    return _sites_in_function(synthetic, rel, source, "<module>")


def _scan_tree(tree: ast.AST, rel: str, source: str) -> list[Site]:
    sites: list[Site] = []

    def walk(node: ast.AST, qual: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{qual}.{node.name}" if qual else node.name
            sites.extend(_sites_in_function(node, rel, source, name))
            for child in node.body:
                walk(child, name)
            return
        if isinstance(node, ast.Lambda):
            name = f"{qual}.<lambda>" if qual else "<lambda>"
            sites.extend(_sites_in_function(node, rel, source, name))
            walk(node.body, name)
            return
        if isinstance(node, ast.ClassDef):
            name = f"{qual}.{node.name}" if qual else node.name
            for child in node.body:
                walk(child, name)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, qual)

    walk(tree, "")
    return sites


def _sites_in_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
    rel: str,
    source: str,
    qual: str,
) -> list[Site]:
    tainted = _tainted_names(func)
    if not tainted:
        return []
    sites: list[Site] = []
    for node in _owned_nodes(func):
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators, strict=False):
                if isinstance(op, (ast.In, ast.NotIn)) and (
                    _expr_tainted(node.left, tainted) or _expr_tainted(comp, tainted)
                ):
                    sites.append(_site(rel, source, node, "in", qual))
                    break
        elif isinstance(node, ast.Call) and _is_regex_call(node) and _call_tainted(node, tainted):
            sites.append(_site(rel, source, node, "regex", qual))
    return sites


def _tainted_names(func: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> set[str]:
    tainted = {name for name in _param_names(func) if _is_question_param(name)}
    if not tainted:
        return tainted
    owned = _owned_nodes(func)
    changed = True
    while changed:
        changed = False
        for node in owned:
            targets: list[ast.AST] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            elif isinstance(node, ast.NamedExpr):
                targets = [node.target]
                value = node.value
            if value is not None and _expr_tainted(value, tainted):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in tainted:
                        tainted.add(target.id)
                        changed = True
    return tainted


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> list[str]:
    args = func.args
    names = [item.arg for item in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    if args.vararg is not None:
        names.append(args.vararg.arg)
    if args.kwarg is not None:
        names.append(args.kwarg.arg)
    return names


def _is_question_param(name: str) -> bool:
    return name in _QUESTION_PARAMS or "question" in name


def _owned_nodes(func: ast.AST) -> list[ast.AST]:
    """Nodes in this function, not in a nested function, class, or lambda."""
    out: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED):
                continue
            out.append(child)
            walk(child)

    walk(func)
    return out


def _expr_tainted(node: ast.AST | None, tainted: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Attribute):
        return node.attr in _STRING_METHODS and _expr_tainted(node.value, tainted)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _STRING_METHODS:
            if _expr_tainted(func.value, tainted):
                return True
        if isinstance(func, ast.Name) and func.id in _WRAPS:
            return any(_expr_tainted(arg, tainted) for arg in node.args)
        return False
    if isinstance(node, ast.BoolOp):
        return any(_expr_tainted(item, tainted) for item in node.values)
    if isinstance(node, ast.IfExp):
        return _expr_tainted(node.body, tainted) or _expr_tainted(node.orelse, tainted)
    if isinstance(node, ast.UnaryOp):
        return _expr_tainted(node.operand, tainted)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _expr_tainted(node.left, tainted) or _expr_tainted(node.right, tainted)
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(item, ast.FormattedValue) and _expr_tainted(item.value, tainted)
            for item in node.values
        )
    if isinstance(node, ast.Subscript):
        return _expr_tainted(node.value, tainted)
    if isinstance(node, ast.NamedExpr):
        return _expr_tainted(node.value, tainted)
    return False


def _is_regex_call(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if isinstance(func.value, ast.Name) and func.value.id == "re":
        return func.attr in _RE_FUNCS
    return func.attr in _REGEX_ON_RECEIVER


def _call_tainted(node: ast.Call, tainted: set[str]) -> bool:
    if any(_expr_tainted(arg, tainted) for arg in node.args):
        return True
    return any(kw.arg != "flags" and _expr_tainted(kw.value, tainted) for kw in node.keywords)


def _site(rel: str, source: str, node: ast.AST, kind: str, qual: str) -> Site:
    raw = ast.get_source_segment(source, node) or ""
    text = " ".join(raw.split())
    if len(text) > 180:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        text = f"{text[:160]}...{digest}"
    return Site(rel, getattr(node, "lineno", 0), kind, qual, text)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    sites = scan_roots()
    if "--check" in args:
        messages = violations(
            sites,
            allow_text=ALLOWLIST_PATH.read_text(encoding="utf-8"),
            ceiling_text=CEILING_PATH.read_text(encoding="utf-8"),
        )
        if messages:
            print("\n".join(messages))
            return 1
        print(f"count={len(sites)}")
        return 0
    print(f"count={len(sites)}")
    for site in sorted(sites, key=lambda item: (item.path, item.line, item.kind)):
        print(site.display())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
