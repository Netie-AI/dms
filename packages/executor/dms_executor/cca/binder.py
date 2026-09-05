"""Certify a proposed term set against landed values. One matching rule for CCA.

Why this shape
--------------
The failure this guards is hard rule 12: a filter that parses, validates,
executes and matches nothing. ``country IN ('Singapore','Malaysia')`` against a
column encoded ``SG`` / ``MY`` returns zero rows, and zero rows summed is a
plausible number under a green badge. So membership is never asserted from a
pack alone. The pack proposes canonical members and their spellings; a granted
column's *distinct landed values* decide which of them exist. Anything the pack
names and the data does not carry is reported as absent, not silently dropped.

Matching is exact on a normalised form, never substring. "crop" appearing
inside "Crop Insurance Services" is a financial product, not agriculture, and a
containment rule cannot tell those apart. Values that match nothing come back in
``unmatched_sample`` so a steward can widen the pack deliberately - coverage is
a separate number from precision, and this module only ever moves precision.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from dms_executor.constraint_cascade import STAGES

#: A column with more distinct values than this is not a categorical encoding.
#: Scanning it whole would read a fact column's payload into memory for nothing.
MAX_DISTINCT = 10_000

#: How many non-matching landed values to carry as evidence. Enough for a
#: steward to see the encoding actually in use; short enough for an envelope.
UNMATCHED_SAMPLE = 12

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def norm_value(value: Any) -> str:
    """Fold a landed value or a pack alias onto one comparable form.

    ``'Kuala Lumpur '`` -> ``'kuala lumpur'``; ``'SKU-BETA'`` -> ``'sku beta'``;
    ``'Côte d'Ivoire'`` -> ``'cote d ivoire'``. Case, padding, punctuation and
    accents are encoding noise. Word order and word content are not, so they
    survive.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _NON_ALNUM.sub(" ", text.casefold())
    return " ".join(text.split())


@dataclass(frozen=True)
class TermPack:
    """A reviewable proposal: canonical members and the spellings that mean them.

    ``column_names`` are the column names this kind of encoding is known to live
    under. A pack never names rows and never names tables - those come from the
    Space grant, so a pack cannot widen what a caller may read.
    """

    name: str
    kind: str
    column_names: tuple[str, ...]
    members: Mapping[str, tuple[str, ...]]
    note: str = ""

    def alias_index(self) -> dict[str, str]:
        """Normalised alias -> canonical member. The member's own name is an alias."""
        index: dict[str, str] = {}
        for canonical, aliases in self.members.items():
            for alias in (canonical, *aliases):
                key = norm_value(alias)
                if key:
                    index.setdefault(key, canonical)
        return index


@dataclass(frozen=True)
class LandedColumn:
    """Distinct values actually present in one granted table's column."""

    table: str
    column: str
    values: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass(frozen=True)
class BinderResult:
    """One cascade stage's verdict, plus everything a human needs to check it."""

    stage: str
    constraint_id: str
    candidate: str
    pack: str
    status: str
    matched: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    absent: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    unmatched_sample: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    polarity: str = "include"

    @property
    def certified(self) -> bool:
        return self.status == "CERTIFIED"

    @property
    def values(self) -> tuple[str, ...]:
        """Landed values to filter on, exactly as the column spells them."""
        out: list[str] = []
        for spellings in self.matched.values():
            for value in spellings:
                if value not in out:
                    out.append(value)
        return tuple(out)

    def binding_text(self) -> str | None:
        if not self.certified or not self.columns:
            return None
        op = "NOT IN" if self.polarity == "exclude" else "IN"
        listed = ", ".join(repr(v) for v in self.values)
        return f"{self.columns[0]} {op} ({listed})"

    def evidence_lines(self) -> list[str]:
        """What was read, what matched, and what the data did not carry."""
        lines = [f"pack={self.pack}"]
        lines += [f"column={ref}" for ref in self.columns]
        for canonical, spellings in self.matched.items():
            lines.append(f"member={canonical} as {', '.join(spellings)}")
        if self.absent:
            lines.append(f"absent_from_data={', '.join(self.absent)}")
        if self.unmatched_sample:
            lines.append(f"unmatched_values={', '.join(self.unmatched_sample)}")
        return lines

    def coverage_note(self) -> str:
        """One plain sentence naming what was included and what was not.

        This is the sentence the buyer reads. An answer that quietly covered 3
        of 11 countries and called itself "SEA" is the confident-wrong case the
        epic exists to prevent, so absence is stated, never omitted.
        """
        if not self.certified:
            return f"{self.candidate}: not bound. {'; '.join(self.reasons)}"
        included = ", ".join(self.matched)
        where = ", ".join(self.columns)
        total = len(self.matched) + len(self.absent)
        note = (
            f"{self.candidate} covered {len(self.matched)} of {total} "
            f"members via {where}: {included}"
        )
        if self.absent:
            note += f". Not present in this data: {', '.join(self.absent)}"
        return note

    def to_constraint(self) -> dict[str, Any]:
        """CCA-01 constraint shape. Parsed by ``constraint_cascade.parse_trace``."""
        return {
            "constraint_id": self.constraint_id,
            "type": self.stage,
            "candidate": self.candidate,
            "binding": self.binding_text(),
            "evidence": self.evidence_lines(),
            "status": self.status,
            "reasons": list(self.reasons),
        }


def _distinct_values(
    con: duckdb.DuckDBPyConnection, table: str, column: str
) -> tuple[str, ...] | None:
    """Distinct non-null values of one column, or None when it is not categorical."""
    try:
        rows = con.execute(
            f'SELECT DISTINCT CAST("{column}" AS VARCHAR) FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT {MAX_DISTINCT + 1}'
        ).fetchall()
    except Exception:  # noqa: BLE001 - a table the grant lists but the file lacks
        return None
    if len(rows) > MAX_DISTINCT:
        return None
    return tuple(str(r[0]) for r in rows)


def scan_landed_columns(
    warehouse: Path | str,
    *,
    tables: Iterable[str],
    column_names: Sequence[str],
) -> list[LandedColumn]:
    """Read the distinct values of every granted table column the pack can bind.

    ``tables`` is the Space grant. This function never widens it: a table the
    caller did not pass is never opened, so a pack cannot reach across a Space
    boundary.
    """
    wanted = {name.casefold() for name in column_names}
    out: list[LandedColumn] = []
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        for table in tables:
            ident = str(table).split(".")[-1]
            if not _IDENT.match(ident):
                continue
            try:
                cols = con.execute(f'SELECT * FROM "{ident}" LIMIT 0').description or []
            except Exception:  # noqa: BLE001 - granted but absent from this file
                continue
            for desc in cols:
                column = str(desc[0])
                if column.casefold() not in wanted:
                    continue
                values = _distinct_values(con, ident, column)
                if values is None:
                    continue
                out.append(LandedColumn(table=ident, column=column, values=values))
    finally:
        con.close()
    return out


def certify_pack(
    *,
    stage: str,
    constraint_id: str,
    candidate: str,
    pack: TermPack,
    warehouse: Path | str | None,
    tables: Iterable[str],
    polarity: str = "include",
) -> BinderResult:
    """CERTIFY a membership binding against landed values, or ABSTAIN saying why.

    Never REFUSE on absence: absence of an encoding is a gap in the data, which
    the caller can fix by landing a column, not a policy violation. REFUSE is
    reserved for a caller error - an unknown stage - which no data change fixes.
    """
    if stage not in STAGES:
        return BinderResult(
            stage=STAGES[0],
            constraint_id=constraint_id,
            candidate=candidate,
            pack=pack.name,
            status="REFUSE",
            reasons=(f"unknown cascade stage {stage!r}",),
            polarity=polarity,
        )
    table_list = tuple(dict.fromkeys(str(t).split(".")[-1] for t in tables))
    if warehouse is None or not table_list:
        return BinderResult(
            stage=stage,
            constraint_id=constraint_id,
            candidate=candidate,
            pack=pack.name,
            status="ABSTAIN",
            tables=table_list,
            reasons=("no granted table to certify against",),
            polarity=polarity,
        )

    columns = scan_landed_columns(
        warehouse, tables=table_list, column_names=pack.column_names
    )
    if not columns:
        return BinderResult(
            stage=stage,
            constraint_id=constraint_id,
            candidate=candidate,
            pack=pack.name,
            status="ABSTAIN",
            tables=table_list,
            reasons=(
                f"no {pack.kind} encoding in this Space: none of "
                f"{', '.join(pack.column_names)} is a column of "
                f"{', '.join(table_list)}",
            ),
            polarity=polarity,
        )

    index = pack.alias_index()
    matched: dict[str, list[str]] = {}
    used_columns: list[str] = []
    unmatched: list[str] = []
    for landed in columns:
        hit = False
        for value in landed.values:
            canonical = index.get(norm_value(value))
            if canonical is None:
                if value not in unmatched:
                    unmatched.append(value)
                continue
            hit = True
            spellings = matched.setdefault(canonical, [])
            if value not in spellings:
                spellings.append(value)
        if hit:
            used_columns.append(landed.ref)

    if not matched:
        scanned = ", ".join(c.ref for c in columns)
        return BinderResult(
            stage=stage,
            constraint_id=constraint_id,
            candidate=candidate,
            pack=pack.name,
            status="ABSTAIN",
            tables=table_list,
            columns=tuple(c.ref for c in columns),
            unmatched_sample=tuple(unmatched[:UNMATCHED_SAMPLE]),
            reasons=(
                f"{scanned} carries no value matching any {pack.name} member; "
                "membership was proposed, never landed",
            ),
            polarity=polarity,
        )

    absent = tuple(m for m in pack.members if m not in matched)
    return BinderResult(
        stage=stage,
        constraint_id=constraint_id,
        candidate=candidate,
        pack=pack.name,
        status="CERTIFIED",
        matched={k: tuple(v) for k, v in matched.items()},
        absent=absent,
        columns=tuple(used_columns),
        tables=table_list,
        unmatched_sample=tuple(unmatched[:UNMATCHED_SAMPLE]),
        reasons=(),
        polarity=polarity,
    )


__all__ = [
    "MAX_DISTINCT",
    "UNMATCHED_SAMPLE",
    "BinderResult",
    "LandedColumn",
    "TermPack",
    "certify_pack",
    "norm_value",
    "scan_landed_columns",
]
