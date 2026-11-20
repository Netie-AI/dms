"""BIRD-ALLOWLIST-01 (dms#304): fail-closed table allowlist for BIRD scoring.

One allowlist file (`tests/fixtures/bird_minidev/table_allowlist.yaml`), sourced
from the counts-only PII scan. A BIRD question is scored only when every table
its gold SQL reads is listed there and it reads no excluded column. Anything
else keeps its place in n as a named ABSTAIN:

  excluded_table:<table>            a table that is not on the allowlist
  excluded_table:(unparsed)         gold SQL that is missing or cannot be settled
  excluded_column:<table>.<column>  a listed table, an excluded column

An excluded question returns zero rows and is never sent to a model. It is
never OK, LAYER, WRONG or INVALID. The table reason wins over the column
reason; the column is still counted.

The table check is not a second parser. It is `dms_executor.grant_struct
.serve_gap`, the product's own structural grant check, with the allowlist as
the grant: every relation in every CTE, subquery, join and set operation must
be listed, and a statement it cannot settle refuses.

The grant preflight is the other half. Picking questions does not keep flagged
values away from a model, because the generate context samples values from any
granted table whose name matches the question. So before any live call the
Space's table names are read, and one flagged table there blocks the run.
Flagged tables come from the committed scan counts
(`tests/fixtures/pii_hold/flagged_columns.csv`), not from a list typed here.
"""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = ROOT / "tests" / "fixtures" / "bird_minidev" / "table_allowlist.yaml"
FLAGGED_COLUMNS = ROOT / "tests" / "fixtures" / "pii_hold" / "flagged_columns.csv"
BRONZE_PREFIX = "bronze.public_"
MEASURE = "bird_allowlist_v1"
UNPARSED = "(unparsed)"
EXCLUDED_TABLE = "excluded_table:"
EXCLUDED_COLUMN = "excluded_column:"
NOT_COMPARABLE = "new measure; not comparable with 73/75"
# dms#304: the scan this allowlist is cut from. Pinned here, not read from the
# file it polices: an edited file cannot raise the count, drop a held table or
# name another scan. The sha256 is the one scripts/pii_mask_check.py pins.
SCAN_FILE = "pii_counts.csv"
SCAN_SHA256 = "878f664d1d920bee99b3859285dd669b86edc16945288a5251c2fa66fae0c330"
SCAN_ZERO_HIT_TABLES = 31
HELD_TABLES = frozenset({"trans", "yearmonth"})
# serve_gap codes that share the ``ungranted:`` prefix but name no table.
_NO_TABLE = frozenset({"file", "unparsed"})


class AllowlistError(ValueError):
    """The allowlist file cannot be trusted. Callers stop; nothing is scored."""


@dataclass(frozen=True)
class TableAllowlist:
    tables: frozenset[str]
    held: frozenset[str]
    flagged: frozenset[str]
    excluded_columns: frozenset[tuple[str, str]]
    scan_sha256: str
    zero_hit_tables: int
    path: str


@dataclass(frozen=True)
class Exclusion:
    """Why a question is not scored. ``columns`` is every excluded column the
    gold SQL reads, counted in the report even when the table reason wins."""

    reason: str
    detail: str
    columns: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()


def _name_ok(name: str) -> bool:
    return (
        bool(name) and name.isascii() and name == name.lower() and name.replace("_", "a").isalnum()
    )


def _stem(entry: Any, where: str) -> str:
    """``bronze.public_<table>`` -> ``<table>``. Any other spelling is refused."""
    text = entry if isinstance(entry, str) else ""
    name = text[len(BRONZE_PREFIX) :] if text.startswith(BRONZE_PREFIX) else ""
    if not _name_ok(name):
        raise AllowlistError(f"{where}: {entry!r} is not a lower-case {BRONZE_PREFIX}<table> name")
    if name.startswith("public_"):
        # The bare grant key for this stem would be `public_<x>`, which is how
        # the bronze table `<x>` itself is spelled. That could list a flagged table.
        raise AllowlistError(f"{where}: {entry!r} repeats the public_ prefix")
    return name


def _stems(raw: Any, where: str) -> frozenset[str]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise AllowlistError(f"{where} must be a list")
    out = [_stem(item, where) for item in raw]
    if len(out) != len(set(out)):
        raise AllowlistError(f"{where} has a duplicate entry")
    return frozenset(out)


def flagged_tables(path: Path = FLAGGED_COLUMNS) -> frozenset[str]:
    """Tables with a nonzero scan cell, from the committed counts-only fixture."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            names = {
                str(row.get("table") or "")[len(BRONZE_PREFIX) :].casefold()
                for row in csv.DictReader(handle)
                if str(row.get("table") or "").startswith(BRONZE_PREFIX)
            }
    except OSError as exc:
        raise AllowlistError(f"flagged-table scan fixture unreadable: {exc}") from exc
    names.discard("")
    if not names:
        raise AllowlistError(f"no flagged {BRONZE_PREFIX}* table in {path.name}: refusing to run")
    return frozenset(names)


def load_table_allowlist(
    path: Path = DEFAULT_ALLOWLIST, *, flagged_csv: Path = FLAGGED_COLUMNS
) -> TableAllowlist:
    try:
        import yaml

        class _OneKeyEach(yaml.SafeLoader):
            """A key written twice is refused: PyYAML would keep the last one."""

        def _mapping(loader: Any, node: Any) -> dict[Any, Any]:
            keys = [loader.construct_object(key, deep=True) for key, _ in node.value]
            if len(keys) != len(set(keys)):
                raise AllowlistError("allowlist repeats a key")
            return dict(yaml.SafeLoader.construct_mapping(loader, node, deep=True))

        _OneKeyEach.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_OneKeyEach)  # noqa: S506
    except AllowlistError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AllowlistError(f"allowlist unreadable: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise AllowlistError("allowlist must be a mapping")
    if data.get("measure") != MEASURE:
        raise AllowlistError(f"allowlist measure must be {MEASURE}")
    scan = data.get("scan")
    if not isinstance(scan, dict):
        raise AllowlistError("allowlist needs a scan block")
    if scan.get("file") != SCAN_FILE:
        raise AllowlistError(f"scan.file must be {SCAN_FILE}")
    sha = scan.get("sha256")
    if sha != SCAN_SHA256:
        raise AllowlistError(f"scan.sha256 must be {SCAN_SHA256}, the scan this is cut from")
    zero_hit = scan.get("zero_hit_tables")
    if isinstance(zero_hit, bool) or zero_hit != SCAN_ZERO_HIT_TABLES:
        raise AllowlistError(f"scan.zero_hit_tables must be {SCAN_ZERO_HIT_TABLES} (the scan)")
    tables = _stems(data.get("tables"), "tables")
    held = _stems(data.get("held"), "held")
    if held != HELD_TABLES:
        raise AllowlistError(f"held must be exactly {sorted(HELD_TABLES)}")
    flagged = flagged_tables(flagged_csv)
    if len(tables) > zero_hit:
        raise AllowlistError(
            f"tables lists {len(tables)} but the scan has {zero_hit} zero-hit tables: "
            "the allowlist is never widened to raise n"
        )
    for label, blocked in (("flagged by the scan", flagged), ("held", held)):
        both = sorted(tables & blocked)
        if both:
            raise AllowlistError(f"tables lists {both}, which is {label}")
    raw_cols = data.get("excluded_columns")
    if raw_cols is None:
        raw_cols = []
    if not isinstance(raw_cols, list):
        raise AllowlistError("excluded_columns must be a list")
    cols: set[tuple[str, str]] = set()
    for item in raw_cols:
        text = item if isinstance(item, str) else ""
        table_part, _, column = text.rpartition(".")
        if not _name_ok(column):
            raise AllowlistError(
                f"excluded_columns: {item!r} is not {BRONZE_PREFIX}<table>.<column>"
            )
        cols.add((_stem(table_part, "excluded_columns"), column))
    return TableAllowlist(
        tables=tables,
        held=held,
        flagged=flagged,
        excluded_columns=frozenset(cols),
        scan_sha256=sha,
        zero_hit_tables=zero_hit,
        path=str(path),
    )


def allowlist_meta(allowlist: TableAllowlist | None) -> dict[str, Any]:
    """What the report says about the allowlist. Names and counts only."""
    if allowlist is None:
        return {"applied": False, "note": "table allowlist not applied: not a BIRD measure"}
    listed = sorted(allowlist.tables) + sorted(
        ".".join(pair) for pair in allowlist.excluded_columns
    )
    return {
        "applied": True,
        "id": hashlib.sha256("\n".join(listed).encode()).hexdigest(),
        "measure": MEASURE,
        "comparable_with_73_75": False,
        "scan_sha256": allowlist.scan_sha256,
        "tables_listed": len(allowlist.tables),
        "zero_hit_tables": allowlist.zero_hit_tables,
        "held": sorted(allowlist.held),
        "flagged": sorted(allowlist.flagged),
        "excluded_columns": len(allowlist.excluded_columns),
    }


# --- question label ---------------------------------------------------------


def _fold(text: Any) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def _table_stem(table: Any) -> str:
    name = _fold(table.name)
    if _fold(table.db) == "bronze" and name.startswith("public_"):
        return name[len("public_") :]
    return name


def _plain(name: str) -> bool:
    return bool(name) and name.isascii() and name.replace("_", "a").isalnum()


def _parse(sql: str, dialect: str) -> Any:
    from dms_executor.grant_struct import sqlglot_dialect
    from sqlglot import parse_one

    return parse_one(sql, read=sqlglot_dialect(dialect))


def _unlisted_tables(tree: Any, allowlist: TableAllowlist) -> tuple[str, ...]:
    """Every table the statement reads that is not listed. CTE names are not tables."""
    from sqlglot import exp

    ctes = {_fold(cte.alias_or_name) for cte in tree.find_all(exp.CTE)}
    names = set()
    for table in tree.find_all(exp.Table):
        stem = _table_stem(table)
        if stem and stem not in ctes and stem not in allowlist.tables:
            names.add(stem if _plain(stem) else UNPARSED)
    return tuple(sorted(names))


def _unmodelled(tree: Any) -> str:
    """Name of a function the parser has no type for, else empty.

    Such a function is opaque: Postgres ``query_to_xml('select ...')`` and
    ``table_to_xml('t', ...)`` read a table named inside a string, which no
    relation check can see. A statement that calls one is not settled.
    """
    from sqlglot import exp

    for node in tree.find_all(exp.Anonymous, exp.AnonymousAggFunc):
        return str(node.name or "?")
    return ""


def _column_hits(tree: Any, allowlist: TableAllowlist) -> tuple[str, ...]:
    """Excluded columns the statement reads, or cannot be shown not to read.

    Wide on purpose: a miss would score a question the scan excluded, an extra
    hit only abstains. For a statement that reads a table with an excluded
    column, the column counts as read unless every name in the statement is a
    plain identifier that is not that column (or is that name qualified by
    another real table's alias), and the statement has no way to read a column
    without naming it: ``*`` or ``alias.*`` (``COUNT(*)`` aside), ``COLUMNS()``,
    a positional column, a NATURAL join, a column list on an alias
    (``t(a, b)`` renames by position), or a relation alias used as a value.
    """
    if not allowlist.excluded_columns:
        return ()
    from sqlglot import exp

    aliases: dict[str, set[str]] = {}
    read: set[str] = set()
    for table in tree.find_all(exp.Table):
        stem = _table_stem(table)
        read.add(stem)
        aliases.setdefault(_fold(table.alias_or_name), set()).add(stem)
        aliases.setdefault(_fold(table.name), set()).add(stem)
    relation_names = set(aliases)
    renamed = False
    for alias in tree.find_all(exp.TableAlias):
        relation_names.add(_fold(alias.name))
        renamed = renamed or bool(alias.columns)

    def other_table(qualifier: Any, stem: str) -> bool:
        owners = aliases.get(_fold(qualifier))
        return bool(qualifier) and owners is not None and stem not in owners

    unnamed = (
        renamed
        or any(join.args.get("method") == "NATURAL" for join in tree.find_all(exp.Join))
        or any(True for _ in tree.find_all(exp.Columns, exp.PositionalColumn))
    )
    hits: set[str] = set()
    for stem, column in sorted(allowlist.excluded_columns):
        if stem not in read:
            continue
        hit = unnamed
        for star in tree.find_all(exp.Star):
            parent = star.parent
            if isinstance(parent, exp.Count):
                continue
            qualifier = parent.table if isinstance(parent, exp.Column) else ""
            if not other_table(qualifier, stem):
                hit = True
        for ident in tree.find_all(exp.Identifier):
            parent = ident.parent
            if isinstance(parent, (exp.Table, exp.TableAlias)):
                continue
            is_column = isinstance(parent, exp.Column) and parent.this is ident
            name = _fold(ident.name)
            if not _plain(name):
                hit = True  # a spelling this cannot compare (escapes, symbols)
            if is_column and not parent.table and name in relation_names:
                hit = True  # a relation alias as a whole-row value
            if name != column:
                continue
            if is_column and other_table(parent.table, stem):
                continue
            hit = True
        if hit:
            hits.add(f"{stem}.{column}")
    return tuple(sorted(hits))


def classify(sql: str, allowlist: TableAllowlist, *, dialect: str) -> Exclusion | None:
    """None when the gold SQL reads only listed tables and no excluded column.

    Everything this cannot settle is an exclusion: missing SQL, a statement
    the grant check refuses for any reason, a checker that cannot be imported
    or raises.
    """
    text = str(sql or "").strip()
    if not text:
        return Exclusion(EXCLUDED_TABLE + UNPARSED, "no_gold_sql")
    grantable: set[str] = set()
    for stem in allowlist.tables:
        grantable.add(stem)
        grantable.add(BRONZE_PREFIX + stem)
    try:
        from dms_executor.grant_struct import serve_gap

        gap = serve_gap(text, grantable=grantable, dialect=dialect, cite_missing=True)
    except Exception as exc:  # noqa: BLE001
        return Exclusion(EXCLUDED_TABLE + UNPARSED, f"checker_error:{type(exc).__name__}")
    columns: tuple[str, ...] = ()
    unlisted: tuple[str, ...] = ()
    try:
        tree = _parse(text, dialect)
        opaque = _unmodelled(tree)
        columns = _column_hits(tree, allowlist)
        unlisted = _unlisted_tables(tree, allowlist)
    except Exception as exc:  # noqa: BLE001
        if not gap:
            return Exclusion(EXCLUDED_TABLE + UNPARSED, f"shape_check_error:{type(exc).__name__}")
        opaque = ""
    if gap:
        name = gap[len("ungranted:") :] if gap.startswith("ungranted:") else ""
        # A name that is not a plain identifier, or that is a listed table read
        # from another schema, is not printed as if it were that table.
        if _plain(name) and name not in _NO_TABLE and name.casefold() not in allowlist.tables:
            tables = tuple(sorted({name.casefold(), *unlisted}))
            return Exclusion(EXCLUDED_TABLE + name, gap, columns, tables)
        return Exclusion(EXCLUDED_TABLE + UNPARSED, gap, columns, unlisted or (UNPARSED,))
    if opaque:
        return Exclusion(EXCLUDED_TABLE + UNPARSED, f"unmodelled_function:{opaque}", columns)
    if columns:
        return Exclusion(EXCLUDED_COLUMN + columns[0], "excluded_column", columns)
    return None


def exclusion_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """n both ways and the excluded counts by table and by column."""
    by_table: Counter[str] = Counter()
    by_column: Counter[str] = Counter()
    excluded = gold_error = 0
    for row in rows:
        reason = str(row.get("exclusion") or "")
        if not reason:
            # The main score line drops a gold error from n. So does this one.
            gold_error += str(row.get("verdict") or "") == "GOLD_ERROR"
            continue
        excluded += 1
        if reason.startswith(EXCLUDED_TABLE):
            # Every unlisted table the question reads is counted; the label
            # names one. A row from before ``excluded_tables`` counts its label.
            for table in row.get("excluded_tables") or [reason[len(EXCLUDED_TABLE) :]]:
                by_table[str(table)] += 1
        for column in row.get("excluded_columns") or []:
            by_column[str(column)] += 1
    return {
        "n": len(rows),
        "n_without_excluded": len(rows) - excluded - gold_error,
        "excluded": excluded,
        "gold_error": gold_error,
        "excluded_by_table": dict(sorted(by_table.items())),
        "excluded_by_column": dict(sorted(by_column.items())),
    }


def allowlist_lines(meta: Mapping[str, Any], counts: Mapping[str, Any]) -> list[str]:
    """The report lines a reader sees next to the score."""
    if not meta.get("applied"):
        return ["table_allowlist=NOT APPLIED. Not a BIRD measure. Do not quote."]
    lines = [
        f"measure={meta['measure']} ({NOT_COMPARABLE})",
        f"table_allowlist tables={meta['tables_listed']} of {meta['zero_hit_tables']} "
        f"zero-hit scan tables. scan sha256={meta['scan_sha256']}",
        f"n={counts['n']} n_without_excluded={counts['n_without_excluded']} "
        f"excluded={counts['excluded']} (named ABSTAIN, no model call, never OK) "
        f"gold_error={counts.get('gold_error', 0)} (not scored)",
    ]
    if meta["tables_listed"] < meta["zero_hit_tables"]:
        lines.append(
            f"table_allowlist INCOMPLETE: {meta['zero_hit_tables'] - meta['tables_listed']} "
            "zero-hit tables not listed yet. Their questions abstain."
        )
    for label, key in (("table", "excluded_by_table"), ("column", "excluded_by_column")):
        for name, count in (counts.get(key) or {}).items():
            lines.append(f"  excluded by {label} {name} {count}")
    return lines


# --- grant preflight --------------------------------------------------------


def _tokens(name: str) -> list[str] | None:
    """Identifier-shaped pieces of a table label. None when it is not ASCII."""
    text = _fold(name)
    if not text.isascii():
        return None
    pieces = "".join(ch if ch.isalnum() or ch == "_" else " " for ch in text).split()
    # schools2, users__1_, post_history: the same table under a copy's name.
    parts = [p for piece in pieces for p in re.split(r"[_0-9]+", piece) if p]
    return pieces + parts + ["".join(parts)]


def flagged_grant_names(names: Iterable[Any], flagged: Iterable[str]) -> list[str]:
    """Granted or bronze names that are, or cannot be shown not to be, flagged.

    Wide on purpose: any casing, schema, quoting or prefix of a flagged table
    matches, and a label that is not plain ASCII counts as flagged.
    """
    flags = sorted({_fold(flag) for flag in flagged})
    hits: list[str] = []
    for raw in names:
        label = str(raw or "").strip()
        if not label:
            continue
        pieces = _tokens(label)
        if pieces is None or any(
            piece == flag or piece.endswith("_" + flag) for piece in pieces for flag in flags
        ):
            hits.append(label)
    return hits


def _grant_stem(label: str) -> str | None:
    """The listed-table stem a granted name is, or None.

    Only ``bronze.public_<t>``, ``public_<t>`` and ``<t>`` (any case, quotes
    dropped) name a table. Anything else, a collision suffix
    (``public_users_1a2b3c4d``), a view or copy (``schools_view``) or another
    schema, is not shown to be a listed table and so cannot pass.
    """
    text = _fold(label).replace('"', "").replace("`", "")
    if text.startswith("bronze."):
        text = text[len("bronze.") :]
    if text.startswith("public_"):
        text = text[len("public_") :]
    return text if _plain(text) else None


def grant_preflight(status: str, names: Sequence[Any], allowlist: TableAllowlist) -> str | None:
    """None when the Space's tables were read and every one is a listed table.

    Fail closed (dms#304 acceptance 4): the generate context samples values from
    any granted table whose name matches words in the question, and DMS itself
    renames a bronze table on a name collision. A blocklist of flagged names
    cannot see every spelling, so a grant passes only when it is on the list.
    """
    if not isinstance(names, (list, tuple)):
        return "the Space's table names are not a list, so no flagged grant can be ruled out"
    if status != "ok":
        return "the Space's table names could not be read, so no flagged grant can be ruled out"
    labels = [str(raw or "").strip() for raw in names]
    if not any(labels):
        return "the Space lists no table, so no flagged grant can be ruled out"
    hits = flagged_grant_names(names, allowlist.flagged)
    if hits:
        return "flagged table grantable on this Space: " + ", ".join(sorted(hits))
    unlisted = sorted(
        label for label in labels if label and _grant_stem(label) not in allowlist.tables
    )
    if unlisted:
        return "table grantable on this Space is not on the table allowlist: " + ", ".join(unlisted)
    return None


# --- self-check plants ------------------------------------------------------


def allowlist_self_check() -> list[str]:
    """Plants for `score_bird.py --self-check`. Each rule rejects and accepts."""
    errs: list[str] = []
    try:
        committed = load_table_allowlist()
    except AllowlistError as exc:
        return [f"committed table_allowlist.yaml must load: {exc}"]
    if committed.tables & (committed.flagged | committed.held):
        errs.append("committed allowlist lists a flagged or held table")
    if committed.held != {"trans", "yearmonth"} or committed.zero_hit_tables != 31:
        errs.append("committed allowlist must hold trans and yearmonth and pin 31 zero-hit tables")
    plant = TableAllowlist(
        tables=frozenset({"gender", "superhero"}),
        held=frozenset({"trans"}),
        flagged=committed.flagged,
        excluded_columns=frozenset({("superhero", "full_name")}),
        scan_sha256=committed.scan_sha256,
        zero_hit_tables=2,
        path="plant",
    )

    def label(sql: str) -> str | None:
        found = classify(sql, plant, dialect="postgres")
        return found.reason if found else None

    if label("SELECT COUNT(*) FROM gender") is not None:
        errs.append("a fully allowlisted question must be scored")
    if label("SELECT COUNT(*) FROM gender g JOIN client c ON c.id = g.id") != (
        EXCLUDED_TABLE + "client"
    ):
        errs.append("an unlisted table must be excluded_table:<table>")
    if label("SELEC nope FROM gender") != EXCLUDED_TABLE + UNPARSED:
        errs.append("unparsed gold SQL must be excluded")
    if label("") != EXCLUDED_TABLE + UNPARSED:
        errs.append("missing gold SQL must be excluded")
    if label("SELECT full_name FROM superhero") != EXCLUDED_COLUMN + "superhero.full_name":
        errs.append("an excluded column must be excluded_column:<table>.<column>")
    if label("SELECT id FROM superhero") is not None:
        errs.append("a listed table without the excluded column must be scored")
    if label("SELECT query_to_xml('select 1 from client', true, true, '') FROM gender") is None:
        errs.append("a function that can read a table named in a string must be excluded")
    both = classify(
        "SELECT s.full_name FROM superhero s JOIN client c ON 1 = 1", plant, dialect="postgres"
    )
    if both is None or both.reason != EXCLUDED_TABLE + "client" or not both.columns:
        errs.append("the table reason must win and the column must still be counted")
    if grant_preflight("ok", ["bronze.public_gender"], plant) is not None:
        errs.append("a Space with no flagged table must pass the preflight")
    if grant_preflight("ok", ["bronze.public_gender", "bronze.public_schools"], plant) is None:
        errs.append("a flagged table on the Space must block the run")
    if grant_preflight("snapshot", [], plant) is None:
        errs.append("unreadable grants must block the run")
    return errs
