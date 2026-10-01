"""dms#304 fail-closed BIRD table allowlist, column abstain, and grant preflight.

Reads table and column names from the fixtures. Parses gold SQL. No values.
No on-the-fly masking.

A question is never removed from n. An unlisted table is
`excluded_table:<table>`. A preflight column is `excluded_column:<table>.<col>`.
If both match, the table reason is the one label and the column is still
counted. Neither label is CORRECT and neither adds to a bar.

Swap: a Cortex HTTP allowlist check in front of question_allowed and
grant_block_reason. sqlglot stays the parser until that HTTP check exists.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / "tests" / "fixtures" / "bird_minidev" / "table_allowlist.yaml"
PREFLIGHT_PATH = ROOT / "tests" / "fixtures" / "bird_minidev" / "preflight.yaml"

# ponytail: one sqlglot parse, no CTE scope walk. A CTE name that is also a
# real table is treated as a table. Upgrade: sqlglot scope sources only.


def bare_name(label: str) -> str:
    text = str(label or "").replace('"', "").replace("`", "").strip().lower()
    if not text:
        return ""
    last = text.split(".")[-1]
    if last.startswith("public_"):
        last = last[len("public_") :]
    return last


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def allowlist_bare(path: Path = ALLOWLIST_PATH) -> set[str]:
    data = _load_yaml(path)
    names: set[str] = set()
    for item in data.get("tables") or []:
        bare = bare_name(str(item))
        if bare:
            names.add(bare)
    return names


def flagged_tables(path: Path = PREFLIGHT_PATH) -> set[str]:
    data = _load_yaml(path)
    names: set[str] = set()
    for item in data.get("flagged_tables") or []:
        bare = bare_name(str(item))
        if bare:
            names.add(bare)
    return names


def gold_sql_tables(sql: str) -> set[str] | None:
    """Bare table names, or None when the SQL cannot be parsed (fail closed)."""
    text = str(sql or "").strip()
    if not text:
        return None
    try:
        from sqlglot import exp, parse_one
    except ImportError:
        return None
    try:
        tree = parse_one(text, read="postgres")
    except Exception:
        return None
    if tree is None:
        return None
    cte_names: set[str] = set()
    for cte in tree.find_all(exp.CTE):
        alias = str(cte.alias or "")
        if alias:
            cte_names.add(alias.lower())
    found: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = str(table.name or "")
        if not name:
            return None
        if name.lower() in cte_names:
            continue
        db = str(table.db or "")
        found.add(bare_name(f"{db}.{name}" if db else name))
    if not found or "" in found:
        return None
    return found


def question_allowed(sql: str, allow: set[str] | None = None) -> bool:
    tables = gold_sql_tables(sql)
    if not tables:
        return False
    allowed = allow if allow is not None else allowlist_bare()
    return tables <= allowed


def _cite(path: Path) -> str:
    return f"dms#304 {path.relative_to(ROOT).as_posix()}"


def decide_question(
    sql: str,
    required: tuple[str, ...] | list[str] = (),
    allow: set[str] | None = None,
) -> dict[str, object] | None:
    """One exclusion label, or None when the question is fully allowed.

    Table wins over column when both match. The column list is still returned
    so the per-column count stays complete. None means score it (model allowed).
    """
    listed = allow if allow is not None else allowlist_bare()
    tables = gold_sql_tables(sql)
    columns = touched_exclude_columns(sql, required)
    col_names = [f"{table}.{column}" for table, column in columns]
    if not tables:
        unlisted = ["unparsed"]
    else:
        unlisted = sorted(table for table in tables if table not in listed)
    if unlisted:
        cite = _cite(PREFLIGHT_PATH) if col_names else _cite(ALLOWLIST_PATH)
        return {
            "reason": f"excluded_table:{unlisted[0]}",
            "label": f"excluded_table:{unlisted[0]}",
            "citation": cite,
            "excluded_tables": unlisted,
            "excluded_columns": col_names,
        }
    if columns:
        table, column = columns[0]
        reason = f"excluded_column:{table}.{column}"
        return {
            "reason": reason,
            "label": reason,
            "citation": _cite(PREFLIGHT_PATH),
            "excluded_tables": [],
            "excluded_columns": col_names,
        }
    return None


def exclude_column_pairs(path: Path = PREFLIGHT_PATH) -> list[tuple[str, str]]:
    """(table, column) in preflight file order. Empty when the file is missing."""
    data = _load_yaml(path)
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in data.get("columns") or []:
        text = str(item).strip().lower()
        if "." not in text:
            continue
        table, column = text.split(".", 1)
        pair = (bare_name(table), column.strip())
        if pair[0] and pair[1] and pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def required_columns_of(item) -> tuple[str, ...]:
    if not isinstance(item, dict):
        return ()
    raw = item.get("required_columns") or ()
    if isinstance(raw, str):
        return (raw,)
    try:
        return tuple(str(part) for part in raw)
    except TypeError:
        return ()


def _bind_table(qualifier: str, alias_map: dict[str, str]) -> str:
    key = str(qualifier or "").strip().lower()
    if not key:
        return ""
    return alias_map.get(key, bare_name(key))


def gold_sql_column_touches(
    sql: str,
) -> tuple[set[tuple[str, str]], set[str]] | None:
    """(table, column) refs and tables projected by SELECT *.

    None when the SQL cannot be parsed. Empty SQL is no touch, not a failure.
    Unqualified columns bind to the tables in that SELECT's FROM.
    """
    text = str(sql or "").strip()
    if not text:
        return set(), set()
    try:
        from sqlglot import exp, parse_one
    except ImportError:
        return None
    try:
        tree = parse_one(text, read="postgres")
    except Exception:
        return None
    if tree is None:
        return None
    alias_map: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        name = bare_name(str(table.name or ""))
        if not name:
            continue
        alias_map[name] = name
        alias = str(table.alias or "").strip().lower()
        if alias:
            alias_map[alias] = name
    refs: set[tuple[str, str]] = set()
    stars: set[str] = set()

    def from_tables(select) -> list[str]:
        frm = select.args.get("from")
        if frm is None:
            return []
        names: list[str] = []
        for table in frm.find_all(exp.Table):
            name = bare_name(str(table.name or ""))
            if name:
                names.append(alias_map.get(name, name))
        return names

    def owning_select(node):
        parent = getattr(node, "parent", None)
        while parent is not None and not isinstance(parent, exp.Select):
            parent = getattr(parent, "parent", None)
        return parent

    for col in tree.find_all(exp.Column):
        column = str(col.name or "").strip().lower()
        if not column:
            continue
        bound = _bind_table(str(col.table or ""), alias_map)
        if bound:
            refs.add((bound, column))
            continue
        select = owning_select(col)
        tables = from_tables(select) if select is not None else []
        if not tables:
            refs.add(("", column))
            continue
        for name in tables:
            refs.add((name, column))
    for select in tree.find_all(exp.Select):
        for expr in select.expressions:
            if not isinstance(expr, exp.Star):
                continue
            raw = expr.args.get("table")
            if raw is not None:
                label = _bind_table(str(getattr(raw, "name", None) or raw), alias_map)
                if label:
                    stars.add(label)
                continue
            stars.update(from_tables(select))
    return refs, stars


def touched_exclude_columns(
    sql: str,
    required: tuple[str, ...] | list[str] = (),
) -> list[tuple[str, str]]:
    """Preflight columns this question touches, in file order.

    Parse failure does not invent a column hit. The table label covers that SQL.
    """
    deny = exclude_column_pairs()
    parsed = gold_sql_column_touches(sql)
    refs: set[tuple[str, str]] = set()
    stars: set[str] = set()
    if parsed is not None:
        refs, stars = parsed
    for item in required:
        text = str(item).strip().lower()
        if "." not in text:
            continue
        table, column = text.split(".", 1)
        pair = (bare_name(table), column.strip())
        if pair[0] and pair[1]:
            refs.add(pair)
    found: list[tuple[str, str]] = []
    for table, column in deny:
        unqualified = ("", column) in refs
        if table in stars or (table, column) in refs or (not table and unqualified):
            found.append((table, column))
        elif ("", column) in refs and table:
            # Unqualified column with no FROM still matches the file (fail closed).
            found.append((table, column))
    return found


def exclusion_stats(rows) -> tuple[int, dict[str, int], dict[str, int]]:
    """Excluded question count, plus counts by table and by column."""
    by_table: dict[str, int] = {}
    by_column: dict[str, int] = {}
    excluded = 0
    for row in rows:
        reason = str(row.get("reason") or "")
        tables = [str(name) for name in (row.get("excluded_tables") or [])]
        cols = [str(name) for name in (row.get("excluded_columns") or [])]
        if reason.startswith("excluded_table:") and not tables:
            tables = [reason.split(":", 1)[1]]
        if reason.startswith("excluded_column:") and not cols:
            cols = [reason.split(":", 1)[1]]
        if not tables and not cols:
            continue
        excluded += 1
        for name in tables:
            by_table[name] = by_table.get(name, 0) + 1
        for name in cols:
            by_column[name] = by_column.get(name, 0) + 1
    return excluded, by_table, by_column


def print_exclusion_report(
    n: int,
    n_without: int,
    by_table: dict[str, int],
    by_column: dict[str, int],
) -> None:
    print(f"n={n}")
    print(f"n_without_excluded={n_without}")
    print("excluded by table:")
    if not by_table:
        print("  (none)")
    for key in sorted(by_table):
        print(f"  {key} {by_table[key]}")
    print("excluded by column:")
    if not by_column:
        print("  (none)")
    for key in sorted(by_column):
        print(f"  {key} {by_column[key]}")
    print("new measure: not comparable with 73/75")


def grant_block_reason(names: list[str], path: Path = PREFLIGHT_PATH) -> str | None:
    """BLOCKED when a flagged table is grantable. None when the grants are clear.

    A missing flagged-table list blocks too: we cannot prove the grants are safe.
    """
    flagged = flagged_tables(path)
    if not flagged:
        return "BLOCKED preflight flagged-table list missing"
    bare = {bare_name(name) for name in names}
    bare.discard("")
    hit = sorted(bare & flagged)
    if hit:
        return "BLOCKED flagged table grantable: " + ", ".join(hit)
    return None
