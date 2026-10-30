"""INS-PROOF-01 - business insights that carry their proof (flag ``DMS_INSIGHT_PROOF``).

``scripts/insights.py`` mines concentration, dominance and unknown-bucket
insights from a verified ontology with no model in the loop. This module is
the same miner moved onto the served path's terms, with a proof attached to
every insight it reports:

- every figure in a headline is a **cell** of one executed statement (the
  wrapper below), never a number Python derived;
- the grouped total is **conserved** against a raw total computed with no join
  at all, within the rounding error the measure expression can explain;
- the wrapper runs **twice** through the injected ``submit`` and both runs must
  return the same rows (``item_result_hash`` and ``sql_vote.rows_equal``);
- the ingest watermarks of every table read are taken before the raw total and
  after the second run, and must not move;
- the headline passes a strict gate: after the label strings are removed,
  every numeric token, bare integers included, is a row cell or one of the
  integers ``k`` and ``n`` the template states.

The proof is a SIBLING of the envelope, never a key in it, and
``assert_proof_shape`` admits only hashes, UTC timestamps, identifiers, closed
enum members, finite numbers and definition text copied from the ontology.

Every refusal goes through ``abstain.build_abstain`` with the customer text in
``REFUSALS``. Nothing on main imports this module and the flag defaults off:
with it off ``prove_insights`` returns ``status='off'`` before it opens the
warehouse or calls ``submit``.

Dimensions are declared columns only: the attributes an approved object item
lists, or else the columns ``verify()`` recorded for the object's declared
relation. Key and link columns are identities, not segments, and are left out.
Labels the ontology prefers for a grain the object plays (``GRAIN_COLUMNS``)
are tried first, so two columns that split a measure identically report under
the name a steward would use.

Scope, stated in ``PROOF_SCOPE``: the result hash covers rows before PII
masking, and contract 1.2.0 has no ledger read-back, so ``proof['ledger']`` is
always null.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard

import httpx

from dms_executor import abstain, bronze, executed_trace
from dms_executor.ontology import (
    GRAIN_COLUMNS,
    GRAIN_OBJECTS,
    CompiledQuery,
    Measure,
    Ontology,
    Refusal,
    relation_tables,
    table_is_granted,
)

FLAG = "DMS_INSIGHT_PROOF"
STAGE = "insight_proof"
PROOF_COLUMNS = ("label", "value", "share_pct", "cum_share_pct", "grouped_total")
PROOF_SCOPE = (
    "figures are cells of the executed SQL; grouped total conserved against a no-join "
    "raw total; no per-key part of the measure is negative; executed twice with equal "
    "rows in the same order; result_hash covers rows before PII masking and ignores "
    "order; a null watermark means a refresh during the proof could not be checked; "
    "contract 1.2.0 cannot read a ledger entry back"
)
WATERMARK_UNKNOWN_ASSUMPTION = (
    "no ingest watermark is recorded for at least one table read, so a source refresh "
    "during the proof could not be ruled out"
)
MAX_COMPILES = 200
MAX_LABELS = 50
KINDS = ("concentration", "dominance", "unknown_bucket")
SOURCES = ("approved_store", "demo_ontology")
CONSERVATION_ASSUMPTION = "grouped total equals the raw no-join total within tolerance"
DEMO_ASSUMPTION = "definition from the built-in demo ontology, not a steward-approved item"

#: Closed: code -> the sentence a customer reads. No figure, no table name.
REFUSALS: dict[str, str] = {
    "ontology_unverified": (
        "I can't prove insights here: the ontology has not passed verification "
        "against this data, so no join is known to be safe. No figure was returned."
    ),
    "no_approved_measure": (
        "I can't prove insights here: there is no approved measure to compute. "
        "No figure was returned."
    ),
    "non_additive": (
        "This measure does not add up across groups, so a share of its total would "
        "not mean anything. No figure was returned."
    ),
    "volatile_measure": (
        "This measure depends on the clock or a random draw, so two runs cannot be "
        "shown to agree. No figure was returned."
    ),
    "signed_measure": (
        "This measure has negative parts or a total that is not positive, so a share "
        "of it is not a share. No figure was returned."
    ),
    "pii_dimension": (
        "This breakdown groups by a column marked as personal data. It was not run."
    ),
    "measure_revoked": (
        "A steward revoked this measure's definition. It was not run."
    ),
    "ungranted": (
        "This breakdown reads a table this Space does not grant. It was not run."
    ),
    "not_conserved": (
        "The grouped figures did not add back to the ungrouped total, so they were "
        "not shown."
    ),
    "too_few_groups": (
        "Fewer than two labelled groups came back, so there is nothing to compare."
    ),
    "empty_fact": (
        "The measure has no figure to total in its table, so there is nothing to "
        "compare. No figure was returned."
    ),
    "result_out_of_order": (
        "The query result did not come back in the order it asked for, so a ranking "
        "read from it would not be true. It was not shown."
    ),
    "too_many_groups": (
        "More than fifty labelled groups came back, too many for a share statement "
        "to be useful."
    ),
    "result_changed_between_runs": (
        "The same query returned different rows on two runs, so neither result was "
        "shown."
    ),
    "source_refreshed_during_proof": (
        "The source data was refreshed while the proof was running, so the result "
        "was not shown."
    ),
    "headline_unbacked": (
        "The sentence stated a figure that is not a cell of the query result, so it "
        "was not shown."
    ),
    "cortex_unavailable": (
        "The query engine could not be reached. No figure was returned."
    ),
    "cortex_submit_failed": (
        "The query engine refused or failed the query. No figure was returned."
    ),
    "envelope_invalid": (
        "The answer did not pass the envelope checks, so it was not shown."
    ),
    "insights_budget": (
        "The insight run reached its limit of compiled breakdowns and stopped."
    ),
}

# scripts/brief.py _NUMBER: a numeric token not glued to a word, bare integers included.
_NUMBER = re.compile(r"(?<![A-Za-z_\d.,])\d[\d,]*(?:\.\d+)?(?![A-Za-z_\d])")
# Clock, randomness and sequence reads. CURRENT_TIME also covers CURRENT_TIMESTAMP
# and get_current_time(stamp); LOCALTIME covers LOCALTIMESTAMP.
_VOLATILE = re.compile(
    r"CURRENT_DATE|CURRENT_TIME|LOCALTIME|TRANSACTION_TIMESTAMP|STATEMENT_TIMESTAMP"
    r"|CLOCK_TIMESTAMP|NOW\s*\(|TODAY\s*\(|RANDOM\s*\(|SETSEED\s*\(|UUID\s*\("
    r"|GEN_RANDOM_UUID|NEXTVAL\s*\(|CURRVAL\s*\(",
    re.I,
)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ISO_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TOP_KEYS = frozenset(
    {
        "v", "insight_id", "kind", "sql_hash", "raw_total_sql", "raw_total_sql_hash",
        "result_hash", "reexec_result_hash", "order_hash", "parts_sql_hash",
        "negative_parts", "raw_total", "grouped_total", "tolerance",
        "conserves", "n_groups", "executed_at", "reexecuted_at", "watermarks",
        "definition", "executions", "ledger", "scope",
    }
)
_DEF_KEYS = frozenset(
    {"measure", "grain", "expression", "additive", "description", "item_id",
     "version_id", "source"}
)
_EXEC_KEYS = frozenset({"engine", "result_hash", "row_count"})


def conservation_tolerance(n_groups: int) -> float:
    """Slack ``grouped_total`` may differ from ``raw_total`` by: half a cent per group
    plus one, what ``ROUND(.., 2)`` per group can drift. A fixed function of
    ``n_groups``, so a proof cannot widen its own tolerance."""
    return round(0.005 * int(n_groups) + 0.005, 6)


def insight_proof_enabled() -> bool:
    """``DMS_INSIGHT_PROOF=1`` exactly. Unset, ``0`` and ``true`` are off."""
    return os.environ.get(FLAG, "0").strip() == "1"


class ProofShapeError(ValueError):
    """A proof carried a key or a string the proof grammar does not admit."""


@dataclass(frozen=True)
class ProvenInsight:
    insight_id: str
    kind: str
    measure: str
    dimension: str
    headline: str
    rows: list[dict[str, Any]]
    k: int
    n: int
    sql: str
    envelope: dict[str, Any]
    proof: dict[str, Any]
    trace: Any


@dataclass(frozen=True)
class InsightRun:
    status: str  # 'off' | 'ok' | 'refused'
    proven: tuple[ProvenInsight, ...] = ()
    refused: tuple[dict[str, Any], ...] = ()
    scope: str = PROOF_SCOPE
    #: Candidates that were not a question (compiler refusal other than no_path,
    #: or no template fired). Not customer envelopes; kept so nothing is dropped
    #: without a record.
    skipped: tuple[dict[str, str], ...] = ()


def format_share(cell: Any) -> str:
    """The one cell a headline prints. Monkeypatched by the E4 gate test."""
    return f"{float(cell):.1f}"


# --------------------------------------------------------------------------
# headline gate
# --------------------------------------------------------------------------


def _numeric_cells(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    out: list[float] = []
    for row in rows:
        for v in row.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
    return out


def headline_numbers_backed(
    headline: str,
    rows: Sequence[Mapping[str, Any]],
    allowed_ints: Collection[int],
) -> list[str]:
    """Numeric tokens of ``headline`` that are neither a row cell nor in ``allowed_ints``.

    Label strings are removed first (``SH-100`` is a label, not a hundred).
    Bare integers count: ``73`` beside a ``73.2`` cell is unbacked.
    """
    text = headline
    labels = sorted(
        {str(r.get("label")) for r in rows if r.get("label") is not None},
        key=len,
        reverse=True,
    )
    for label in labels:
        if label:
            # Whole-token only: label ``73`` must not eat the ``73`` of ``73.9``.
            text = re.sub(
                r"(?<![A-Za-z0-9_.,])" + re.escape(label) + r"(?![A-Za-z0-9_]|[.,]\d)",
                " ",
                text,
            )
    cells = _numeric_cells(rows)
    allowed = {int(x) for x in allowed_ints}
    unbacked: list[str] = []
    for token in _NUMBER.findall(text):
        plain = token.replace(",", "")
        if "." not in plain and int(plain) in allowed:
            continue
        num = float(plain)
        if any(abs(num - c) < 1e-9 for c in cells):
            continue
        unbacked.append(token)
    return unbacked


# --------------------------------------------------------------------------
# proof shape
# --------------------------------------------------------------------------


def _finite(x: Any) -> TypeGuard[int | float]:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


def _parse_z(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp[:-1] + "+00:00")


def _bad(msg: str) -> ProofShapeError:
    return ProofShapeError(msg)


def insight_id_for(measure: str, label: str, kind: str, sql_hash: str) -> str:
    """``ins_`` + sha256(measure|label|kind|sql_hash)[:16]: what ``_emit`` assigns."""
    return "ins_" + hashlib.sha256(
        f"{measure}|{label}|{kind}|{sql_hash}".encode()
    ).hexdigest()[:16]


def order_hash(rows: Sequence[Any]) -> str:
    """sha256 of result rows IN ORDER: one ``item_result_hash`` per row, in sequence."""
    from dms_executor.skills_quarantine import item_result_hash

    return hashlib.sha256(
        "\x1e".join(item_result_hash([row]) for row in rows).encode("utf-8")
    ).hexdigest()


def assert_proof_shape(
    proof: Any,
    *,
    onto: Ontology | None = None,
    sql: str | None = None,
    rows: Sequence[Mapping[str, Any]] | None = None,
    label: str | None = None,
) -> None:
    """Raise ``ProofShapeError`` unless ``proof`` is the proof grammar and self-consistent.

    With ``label`` (the insight's dimension) the insight_id must be the hash of
    measure, label, kind and sql_hash, so an id cannot ride on another kind's or
    another statement's proof. Two kinds read from one statement share every
    other proof field, so the id is what tells their proofs apart.

    Shape: closed keys, hashes, UTC timestamps, identifiers, finite numbers.
    Consistency: conserves within tolerance, both runs and both executions share
    one result_hash, row counts agree with ``n_groups``, no negative part.
    With ``onto`` the definition text must equal the ontology's measure verbatim;
    with ``sql`` the sql_hash must hash it; with ``rows`` (the unmasked result)
    result_hash, order_hash and n_groups must describe them. Without ``sql`` and
    ``rows`` the hashes are only checked against each other, not against data.
    """
    from dms_executor.skills_quarantine import item_content_hash

    if not isinstance(proof, dict) or set(proof) != _TOP_KEYS:
        extra = sorted(set(proof) - _TOP_KEYS) if isinstance(proof, dict) else []
        raise _bad(f"proof keys are not the proof grammar (extra: {extra})")
    if proof["v"] != 1 or isinstance(proof["v"], bool):
        raise _bad("v must be 1")
    if proof["kind"] not in KINDS:
        raise _bad("kind is not a closed member")
    if not isinstance(proof["insight_id"], str) or not re.fullmatch(
        r"ins_[0-9a-f]{16}", proof["insight_id"]
    ):
        raise _bad("insight_id")
    for key in ("sql_hash", "raw_total_sql_hash", "result_hash", "reexec_result_hash",
                "order_hash", "parts_sql_hash"):
        if not isinstance(proof[key], str) or not _HEX64.fullmatch(proof[key]):
            raise _bad(f"{key} is not hex64")
    for key in ("executed_at", "reexecuted_at"):
        if not isinstance(proof[key], str) or not _ISO_Z.fullmatch(proof[key]):
            raise _bad(f"{key} is not ISO-8601 UTC")
    if _parse_z(proof["reexecuted_at"]) < _parse_z(proof["executed_at"]):
        raise _bad("reexecuted_at is before executed_at")
    for key in ("raw_total", "grouped_total", "tolerance"):
        if not _finite(proof[key]):
            raise _bad(f"{key} is not a finite number")
    if not isinstance(proof["conserves"], bool):
        raise _bad("conserves is not a bool")
    if not isinstance(proof["n_groups"], int) or isinstance(proof["n_groups"], bool):
        raise _bad("n_groups is not an int")
    if proof["ledger"] is not None:
        raise _bad("ledger must be null: contract 1.2.0 cannot read an entry back")
    if proof["scope"] != PROOF_SCOPE:
        raise _bad("scope is not PROOF_SCOPE")

    marks = proof["watermarks"]
    if not isinstance(marks, dict):
        raise _bad("watermarks is not a dict")
    for table, stamp in marks.items():
        if not isinstance(table, str) or not _IDENT.fullmatch(table):
            raise _bad("watermark table is not an identifier")
        if stamp is not None and (not isinstance(stamp, str) or not _ISO_Z.fullmatch(stamp)):
            raise _bad("watermark is not ISO-8601 UTC or null")

    d = proof["definition"]
    if not isinstance(d, dict) or set(d) != _DEF_KEYS:
        raise _bad("definition keys")
    if d["source"] not in SOURCES:
        raise _bad("definition.source is not a closed member")
    if not isinstance(d["additive"], bool):
        raise _bad("definition.additive is not a bool")
    for key in ("item_id", "version_id"):
        if d[key] is not None and (not isinstance(d[key], str) or not _IDENT.fullmatch(d[key])):
            raise _bad(f"definition.{key} is not an identifier")
    for key in ("measure", "grain", "expression", "description"):
        if not isinstance(d[key], str):
            raise _bad(f"definition.{key} is not text")
    m = onto.measures.get(d["measure"]) if onto is not None else None
    if onto is not None:
        if m is None:
            raise _bad("definition.measure is not in the ontology")
        if (d["grain"], d["expression"], d["description"], d["additive"]) != (
            m.grain, m.expression, m.description, m.additive
        ):
            raise _bad("definition text is not the ontology's, verbatim")
    else:
        for key in ("measure", "grain"):
            if not _IDENT.fullmatch(d[key]):
                raise _bad(f"definition.{key} is not an identifier")

    raw_sql = proof["raw_total_sql"]
    prefix = f"SELECT {d['expression']} AS raw_total FROM "
    if (
        not isinstance(raw_sql, str)
        or not raw_sql.startswith(prefix)
        or not raw_sql.endswith(" f")
        or len(raw_sql) <= len(prefix) + 2
    ):
        raise _bad("raw_total_sql is not the definition's no-join total")
    if item_content_hash(raw_sql) != proof["raw_total_sql_hash"]:
        raise _bad("raw_total_sql_hash does not hash raw_total_sql")
    if onto is not None and m is not None:
        # The relation segment and the item metadata are the ontology's too, not
        # only the prefix: a total over another relation conserves nothing.
        fact = onto.objects.get(m.grain)
        if fact is None or raw_sql != f"{prefix}{fact.relation} f":
            raise _bad("raw_total_sql does not total the measure's own fact relation")
        want = _definition(onto, m, d["source"])
        if (d["item_id"], d["version_id"]) != (want["item_id"], want["version_id"]):
            raise _bad("definition item_id/version_id is not the registry's")
    latest = _parse_z(proof["reexecuted_at"])
    for stamp in marks.values():
        if stamp is not None and _parse_z(stamp) > latest:
            raise _bad("a watermark is later than the proof's own re-execution")
    if sql is not None:
        # Every base table the SQL reads carries a watermark (null when unknown),
        # and none is named that the SQL does not read.
        scoped, read = _scoped_tables(sql), _read_tables(sql)
        if scoped is None or read is None or not scoped <= set(marks) <= read:
            raise _bad("watermarks do not name exactly the tables the SQL reads")

    execs = proof["executions"]
    if not isinstance(execs, list) or len(execs) != 2:
        raise _bad("executions must hold two runs")
    for run in execs:
        if not isinstance(run, dict) or set(run) != _EXEC_KEYS:
            raise _bad("execution keys")
        if run["engine"] != "submit":
            raise _bad("execution engine is not a closed member")
        if not isinstance(run["result_hash"], str) or not _HEX64.fullmatch(run["result_hash"]):
            raise _bad("execution result_hash is not hex64")
        if not isinstance(run["row_count"], int) or isinstance(run["row_count"], bool):
            raise _bad("execution row_count is not an int")

    # Integrity, not only shape: the fields must agree with each other.
    if proof["conserves"] is not True:
        raise _bad("a proof that does not conserve is not a proof")
    slack = float(proof["tolerance"]) + 1e-6
    if abs(float(proof["raw_total"]) - float(proof["grouped_total"])) > slack:
        raise _bad("grouped_total does not conserve raw_total within tolerance")
    if float(proof["raw_total"]) <= 0:
        raise _bad("raw_total is not positive")
    if proof["result_hash"] != proof["reexec_result_hash"]:
        raise _bad("the two runs do not share a result_hash")
    if [r["result_hash"] for r in execs] != [proof["result_hash"], proof["reexec_result_hash"]]:
        raise _bad("executions disagree with the top-level hashes")
    if any(r["row_count"] != proof["n_groups"] for r in execs):
        raise _bad("n_groups disagrees with the executions' row_count")
    if proof["n_groups"] < 2:
        raise _bad("n_groups is below two")
    if abs(float(proof["tolerance"]) - conservation_tolerance(proof["n_groups"])) > 1e-9:
        raise _bad("tolerance is not the conservation tolerance for n_groups")
    if type(proof["negative_parts"]) is not int or proof["negative_parts"] != 0:
        raise _bad("negative_parts must be the integer 0")
    if sql is not None and item_content_hash(sql) != proof["sql_hash"]:
        raise _bad("sql_hash does not hash the executed SQL")
    if label is not None and proof["insight_id"] != insight_id_for(
        d["measure"], label, proof["kind"], proof["sql_hash"]
    ):
        raise _bad("insight_id is not the hash of measure, label, kind and sql_hash")
    if rows is not None:
        from dms_executor.skills_quarantine import item_result_hash

        projected = [{c: r[c] for c in PROOF_COLUMNS} for r in rows]
        if item_result_hash(projected) != proof["result_hash"]:
            raise _bad("result_hash does not hash the rows")
        if order_hash(projected) != proof["order_hash"]:
            raise _bad("order_hash does not hash the rows in order")
        if len(projected) != proof["n_groups"]:
            raise _bad("n_groups disagrees with the rows")
        # The figures, not only the hashes: every row's grouped_total cell is the
        # proof's grouped_total, and the rows' values add back to raw_total.
        cells = [r["grouped_total"] for r in projected]
        if not all(_finite(c) and abs(float(c) - float(proof["grouped_total"])) <= 1e-9
                   for c in cells):
            raise _bad("grouped_total is not the rows' grouped_total cell")
        values = [r["value"] for r in projected]
        if not all(_finite(v) for v in values) or abs(
            sum(float(v) for v in values) - float(proof["raw_total"])
        ) > slack:
            raise _bad("the rows' values do not add back to raw_total within tolerance")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_z(raw: Any) -> str | None:
    """A registry ``extracted_at`` as ISO-8601 UTC, or None when it cannot be read."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # the bronze registry writes UTC
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Refused(Exception):
    def __init__(self, code: str, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.evidence = evidence or {}


def _submit_rows(submit: Callable[[str], Any], sql: str) -> list[dict[str, Any]]:
    from dms_executor.manifest import SecurityEvent, SubmitError, classify_submit_error
    from dms_executor.verified_queries import rows_from_submit_result

    try:
        result = submit(sql)
    except SecurityEvent as exc:
        raise _Refused("ungranted") from exc
    except SubmitError as exc:
        if isinstance(exc.__cause__, (httpx.TransportError, ConnectionError)):
            raise _Refused("cortex_unavailable") from exc
        if exc.code == "path_not_allowed":
            raise _Refused("ungranted") from exc
        raise _Refused(f"cortex_submit_failed:{exc.code}") from exc
    except (httpx.TransportError, ConnectionError, TimeoutError) as exc:
        raise _Refused("cortex_unavailable") from exc
    except Exception as exc:  # noqa: BLE001 - an injected submit; classify, never leak
        err = classify_submit_error(exc)
        if err.code == "path_not_allowed":
            raise _Refused("ungranted") from exc
        raise _Refused(f"cortex_submit_failed:{err.code}") from exc
    # A dict-shaped result carries ``ok`` as a key, not an attribute (verify round
    # 5: ``{'ok': False, 'output': rows}`` was read as a success).
    ok = result.get("ok", True) if isinstance(result, Mapping) else getattr(result, "ok", True)
    if ok is False:
        raise _Refused("cortex_submit_failed:not_ok")
    return rows_from_submit_result(result)


def _project(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if any(c not in row for c in PROOF_COLUMNS):
            raise _Refused("cortex_submit_failed:result_shape")
        out.append({c: row[c] for c in PROOF_COLUMNS})
    return out


def _wrapper_sql(compiled_sql: str, dim_alias: str, measure: str) -> str:
    return (
        f"WITH g AS ({compiled_sql}) SELECT g.{_q(dim_alias)} AS label, "
        f"g.{_q(measure)} AS value,\n"
        "ROUND(100.0*value/SUM(value) OVER (),1) AS share_pct,\n"
        "ROUND(100.0*SUM(value) OVER (ORDER BY (label IS NULL), value DESC, label "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)/SUM(value) OVER (),1) "
        "AS cum_share_pct,\n"
        "SUM(value) OVER () AS grouped_total\n"
        "FROM g ORDER BY (label IS NULL), value DESC, label"
    )


def _parts_sql(m: Measure, fact_rel: str, key: list[str]) -> str | None:
    """Count of fact keys whose own contribution to ``m`` is negative. None: no key."""
    cols = [c for c in key if str(c).strip()]
    if not cols:
        return None
    by = ", ".join(f"f.{_q(c)}" for c in cols)
    return (
        f"SELECT COUNT(*) AS negative_parts FROM (SELECT {m.expression} AS part "
        f"FROM {fact_rel} f GROUP BY {by}) p WHERE p.part < 0"
    )


def _in_declared_order(rows: Sequence[Mapping[str, Any]]) -> bool:
    """True when ``rows`` are in the wrapper's ORDER BY and their cells agree.

    Labelled rows first, value non-increasing, the NULL label last, cum_share_pct
    non-decreasing and within rounding of the running share_pct sum, each
    share_pct within rounding of its value over grouped_total, the last cum 100.
    Python checks the cells here; it never prints a figure it computed.
    """
    if not rows:
        return False
    seen_null = False
    prev_value: float | None = None
    prev_cum = -math.inf
    running = 0.0
    for i, row in enumerate(rows):
        cells = [x for x in (row.get(c) for c in PROOF_COLUMNS[1:]) if _finite(x)]
        if len(cells) != len(PROOF_COLUMNS) - 1:
            return False
        value, share, cum, total = cells
        if float(total) <= 0:
            return False
        if row.get("label") is None:
            seen_null = True
        elif seen_null:
            return False
        else:
            if prev_value is not None and float(value) > prev_value + 1e-9:
                return False
            prev_value = float(value)
        if float(cum) < prev_cum - 1e-9:
            return False
        prev_cum = float(cum)
        if abs(float(share) - 100.0 * float(value) / float(total)) > 0.05 + 1e-6:
            return False
        running += float(share)
        if abs(float(cum) - running) > 0.05 * (i + 2) + 1e-6:
            return False
    return abs(prev_cum - 100.0) <= 0.05 + 1e-6


def _dim_label(obj: str, attr: str, grain: str) -> str:
    """``location_code`` for a column already named after its object, else ``Obj.attr``."""
    if obj == grain:
        return attr
    if attr.casefold().startswith(obj.casefold() + "_"):
        return attr
    return f"{obj}.{attr}"


def _bare(relation: str) -> str:
    text = str(relation or "").strip().strip('"')
    if not text or text.startswith("("):
        return ""
    return text.rsplit(".", 1)[-1].strip('"').casefold()


class _AllColumns(frozenset):
    """Fail closed: every column of the object is treated as personal data."""

    def __contains__(self, item: object) -> bool:
        return True


_ALL_PII = _AllColumns()


# Projection nodes the taint walker models. Each passes its inputs' values
# through by column reference only: none reads a column named by a string, a
# position, or a whole row. Anything else in a projection of a relation that
# reads a marked table makes the object all-PII (verify round 3: ``#2``,
# ``COLUMNS('name')``, ``t['name']`` and ``struct_extract(t, 'name')`` each served
# a marked column under a new name).
def _projection_allowlist() -> tuple[type, ...]:
    from sqlglot import exp

    return (
        exp.Column, exp.Identifier, exp.Literal, exp.Null, exp.Boolean, exp.Paren,
        exp.Cast, exp.TryCast, exp.DataType, exp.DataTypeParam,
        exp.Case, exp.If, exp.Coalesce, exp.Nullif,
        exp.Upper, exp.Lower, exp.Trim, exp.Substring, exp.Length, exp.Concat, exp.DPipe,
        exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg, exp.Abs, exp.Round,
        exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.And, exp.Or, exp.Not,
        exp.Is, exp.In, exp.Between,
        exp.Sum, exp.Count, exp.Min, exp.Max, exp.Avg, exp.Distinct,
    )


def _column_parts(col: Any) -> set[str]:
    """Every name part of a column reference: ``t.name.x`` is field x of column name."""
    return {
        str(part).casefold()
        for part in (col.name, col.table, col.db, col.catalog)
        if isinstance(part, str) and part
    }


def _subquery_taint(relation: str, marked: set[str]) -> set[str] | None:
    """Output names of a subquery relation that carry a ``marked`` column.

    An allowlist, not a denylist. Every projection of every SELECT in the
    statement must be a column, or an alias over an expression built only from
    ``_projection_allowlist`` nodes, and no column may name a table, a table
    alias or a CTE (a whole-row reference carries every column). Anything else
    returns None (all-PII): a positional reference, ``COLUMNS()``, a bracket or
    struct access, an unlisted function, a window, a scalar subquery, a star, a
    set operation, PIVOT/UNPIVOT, VALUES, UNNEST, LATERAL, a table or CTE alias
    column list, an unaliased computed projection, or text that does not parse.

    Over-approximates within the allowlist: an output name is tainted when any
    name part of any column it reads is tainted, to a fixpoint, so
    ``name AS tname``, ``UPPER(name) AS label`` and a nested re-alias all carry
    the mark. Mirrors def_validate, where a star over a pii table fails closed.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError

    from dms_executor.demo_warehouse import SERVING_DIALECT

    text = relation.strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    try:
        tree = sqlglot.parse_one(text, read=SERVING_DIALECT)
    except (SqlglotError, ValueError):
        return None
    if tree is None:
        return None
    if tree.find(exp.Star, exp.SetOperation, exp.Pivot, exp.Values, exp.Unnest, exp.Lateral):
        return None
    if any(ta.columns for ta in tree.find_all(exp.TableAlias)):
        return None
    sources: set[str] = set()
    for table in tree.find_all(exp.Table):
        sources |= {str(x).casefold() for x in (table.name, table.alias) if x}
    for node in tree.find_all(exp.Subquery, exp.CTE):
        if node.alias:
            sources.add(str(node.alias).casefold())
    allowed = _projection_allowlist()
    projections: list[tuple[str, set[str]]] = []
    for select in tree.find_all(exp.Select):
        for proj in select.expressions:
            if isinstance(proj, exp.Alias):
                out, body = str(proj.alias or "").casefold(), proj.this
            elif isinstance(proj, exp.Column):
                out, body = str(proj.name or "").casefold(), proj
            else:
                return None  # an unaliased computed projection: the engine names it
            if not out:
                return None
            for node in body.walk():
                if not isinstance(node, allowed):
                    return None
                if isinstance(node, exp.Column) and str(node.name).casefold() in sources:
                    return None  # a whole-row reference carries every column
            cols: set[str] = set()
            for col in body.find_all(exp.Column):
                cols |= _column_parts(col)
            projections.append((out, cols))
    # Implicit flow (verify round 4): ``JOIN c ON c.code = t.name`` makes c.code
    # carry t.name's values. Every column that shares a predicate conjunct with
    # a tainted column is tainted by its own name.
    predicates: list[tuple[set[str], set[str]]] = []
    for clause in tree.find_all(exp.Join, exp.Where, exp.Having, exp.Qualify):
        conds = [clause.args.get("on")] if isinstance(clause, exp.Join) else [clause.this]
        for cond in conds:
            if cond is None:
                continue
            for conj in (cond.flatten() if isinstance(cond, exp.And) else [cond]):
                refs = list(conj.find_all(exp.Column))
                parts: set[str] = set()
                for col in refs:
                    parts |= _column_parts(col)
                names = {str(col.name).casefold() for col in refs if col.name}
                if names:
                    predicates.append((parts, names))
    tainted = set(marked)
    changed = True
    while changed:
        changed = False
        for out, cols in projections:
            if out not in tainted and cols & tainted:
                tainted.add(out)
                changed = True
        for parts, names in predicates:
            if parts & tainted and not names <= tainted:
                tainted |= names
                changed = True
    return tainted


def _read_tables(sql: str) -> frozenset[str] | None:
    """Bare casefolded tables ``sql`` reads: the regex set and sqlglot's, united.

    ``relation_tables`` sees only names after FROM or JOIN, so a comma join
    (``FROM a, b``) or a spaced dot (``Sales . Territory``) hides a table from
    it. sqlglot's scope walk (``tables_read_checked``) sees those. The union is
    the superset both callers need: every table must be granted, and a pii item
    on any of them applies. None when sqlglot cannot parse or scope the text,
    which callers treat as ungranted and all-PII.
    """
    parsed = _scoped_tables(sql)
    if parsed is None:
        return None
    out = set(relation_tables(sql)) | parsed
    out.discard("")
    return frozenset(out)


def _scoped_tables(sql: str) -> frozenset[str] | None:
    """Bare casefolded base tables sqlglot's scope walk finds; CTE names excluded."""
    from dms_executor.sql_currency import tables_read_checked

    text = (sql or "").strip()
    if not text:
        return None
    if not re.match(r"(?is)^\s*(select|with|from)\b", text):
        text = f"SELECT * FROM {text}"
    try:
        parsed, approximate = tables_read_checked(text)
    except Exception:  # noqa: BLE001 - an unreadable relation fails closed
        return None
    if approximate:
        return None
    return frozenset(t.rsplit(".", 1)[-1].strip('"').casefold() for t in parsed)


def _pii_items(onto: Ontology) -> list[tuple[str, str]]:
    """``(bare table or '', casefolded column)`` for every pii item that names a column.

    The name-only form splits at the LAST dot: ``Sales.Territory.name`` is column
    ``name`` of table ``territory`` (verify round 4: splitting at the first dot made
    it column ``territory.name`` of table ``sales``, which matched nothing).
    """
    out: list[tuple[str, str]] = []
    for payload in onto.__dict__.get("_pii", []) or []:
        if not isinstance(payload, dict):
            continue
        rel = _bare(str(payload.get("relation") or payload.get("table") or ""))
        column = str(payload.get("column") or "").strip().strip('"').casefold()
        name = str(payload.get("name") or "")
        if not column and "." in name:
            left, right = name.rsplit(".", 1)
            rel, column = _bare(left), right.strip().strip('"').casefold()
        if column:
            out.append((rel, column))
    return out


#: Views nest; past this depth a relation is treated as all-PII.
_MAX_VIEW_DEPTH = 8


def _view_body(create_sql: str) -> str | None:
    """The SELECT of a ``CREATE VIEW`` statement, or None when it cannot be read.

    A view with a column list (``CREATE VIEW v (a, b) AS ...``) renames by
    position; its body is returned as ``SELECT * FROM (body) AS _v(a, b)``, which
    the taint walker fails closed on when the body reads a marked column.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError

    from dms_executor.demo_warehouse import SERVING_DIALECT

    try:
        tree = sqlglot.parse_one(create_sql.strip().rstrip(";"), read=SERVING_DIALECT)
    except (SqlglotError, ValueError):
        return None
    if not isinstance(tree, exp.Create) or str(tree.args.get("kind") or "").upper() != "VIEW":
        return None
    body = tree.expression
    if body is None:
        return None
    text = body.sql(dialect=SERVING_DIALECT)
    if isinstance(tree.this, exp.Schema) and tree.this.expressions:
        cols = ", ".join(_q(str(c.name)) for c in tree.this.expressions)
        text = f"SELECT * FROM ({text}) AS _v({cols})"
    return text


def _view_bodies(con: Any) -> dict[str, list[str | None]] | None:
    """Bare casefolded view name -> the SELECT each such view runs. None: unreadable."""
    try:
        rows = con.execute(
            "SELECT view_name, sql FROM duckdb_views() WHERE NOT internal"
        ).fetchall()
    except Exception:  # noqa: BLE001 - a catalog that cannot answer fails closed
        return None
    out: dict[str, list[str | None]] = {}
    for name, create in rows:
        out.setdefault(str(name).casefold(), []).append(_view_body(str(create or "")))
    return out


def _relation_taint(
    relation: str,
    items: list[tuple[str, str]],
    views: Mapping[str, list[str | None]],
    depth: int = 0,
) -> Collection[str]:
    """Casefolded output names of ``relation`` that carry a marked column.

    A base table's marked columns are its pii items. A view's are what its own
    SELECT carries, computed the same way, so a view that renames a marked
    column (``name AS region``) marks ``region`` (verify round 4: a view hid the
    pii item, because sqlglot does not expand views). A view that cannot be
    read, or nests past ``_MAX_VIEW_DEPTH``, makes the relation all-PII.
    """
    tables = _read_tables(relation)
    if not tables:
        return _ALL_PII
    marked = {column for rel, column in items if not rel or rel in tables}
    for table in sorted(tables & set(views)):
        if depth >= _MAX_VIEW_DEPTH:
            return _ALL_PII
        for body in views[table]:
            if body is None:
                return _ALL_PII
            inner = _relation_taint(body, items, views, depth + 1)
            if isinstance(inner, _AllColumns):
                return _ALL_PII
            marked |= set(inner)
    if not marked:
        return marked
    text = relation.strip()
    if text.startswith("(") or " " in text:
        tainted = _subquery_taint(relation, marked)
        return _ALL_PII if tainted is None else tainted
    return marked


def _pii_columns(onto: Ontology, obj: str, con: Any = None) -> Collection[str]:
    """Casefolded columns of ``obj`` a steward marked as personal data.

    A pii item applies when the table it names is one the object's relation
    reads (``_read_tables``: subqueries and comma joins included), or when it
    names no table. With ``con``, a relation that reads a view reads what the
    view reads, and the view's renames carry the mark (``_relation_taint``).
    An object whose tables cannot be resolved, whose subquery does not parse,
    or whose views cannot be listed is all-PII: nothing of it is grouped by.
    """
    items = _pii_items(onto)
    if not items:
        return set()
    relation = str(onto.objects[obj].relation or "")
    views = _view_bodies(con) if con is not None else {}
    if views is None:
        return _ALL_PII
    return _relation_taint(relation, items, views)


def _item_meta(onto: Ontology) -> list[dict[str, Any]]:
    raw = onto.__dict__.get("_item_meta", []) or []
    return [m for m in raw if isinstance(m, dict)]


def _declared_attributes(onto: Ontology, obj: str) -> list[str] | None:
    for meta in _item_meta(onto):
        payload = meta.get("payload")
        if meta.get("kind") != "object" or not isinstance(payload, dict):
            continue
        if str(payload.get("name") or "") != obj:
            continue
        attrs = payload.get("attributes")
        if not isinstance(attrs, list):
            return None
        out: list[str] = []
        for a in attrs:
            if isinstance(a, str) and a.strip():
                out.append(a.strip())
            elif isinstance(a, dict):
                for key in ("source_column", "column", "name"):
                    val = a.get(key)
                    if isinstance(val, str) and val.strip():
                        out.append(val.strip())
                        break
        return out
    return None


def _preferred_labels(onto: Ontology, obj: str) -> list[str]:
    grains = {g for g, target in onto.grain_aliases.items() if target == obj}
    grains |= {g for g, objs in GRAIN_OBJECTS.items() if obj in objs}
    out: list[str] = []
    for g in sorted(grains):
        for col in GRAIN_COLUMNS.get(g, ()):
            if col not in out:
                out.append(col)
    return out


def _identity_columns(onto: Ontology, obj: str) -> set[str]:
    cols = {c.casefold() for c in onto.objects[obj].key}
    for link in onto.links.values():
        if link.from_object == obj:
            cols |= {c.casefold() for c in link.from_columns}
        if link.to_object == obj:
            cols |= {c.casefold() for c in link.to_columns}
    return cols


def _statement_gap(sql: str, granted: set[str]) -> str | None:
    """Main's statement check, ``grant_struct.serve_gap`` in the serving dialect.

    None when ``sql`` is one granted read. ``table_is_granted`` compares bare
    names, so ``Secret.Territory`` passed against a grant of ``Sales.Territory``
    (verify round 5). ``serve_gap`` keeps the qualifier: a qualified relation is
    granted only when that exact key is. It is the check
    ``generative_ask.validate_compiled_sql`` runs before main submits.
    """
    from dms_executor.demo_warehouse import SERVING_DIALECT
    from dms_executor.grant_struct import serve_gap

    try:
        return serve_gap(sql, grantable=set(granted), dialect=SERVING_DIALECT,
                         cite_missing=False)
    except Exception:  # noqa: BLE001 - a check that cannot answer refuses
        return "ungranted"


def _granted_relation(relation: str, granted: set[str]) -> bool:
    tables = _read_tables(relation)
    return (
        bool(tables)
        and all(table_is_granted(t, granted) for t in tables or ())
        and _statement_gap(f"SELECT 1 FROM {relation} f", granted) is None
    )


@dataclass
class _Dims:
    usable: list[str] = field(default_factory=list)
    pii: list[str] = field(default_factory=list)


def _dimensions(con: Any, onto: Ontology, obj: str) -> _Dims:
    """Declared, VARCHAR, segment-shaped columns of ``obj``; PII ones are never read."""
    rel = onto.objects[obj].relation
    declared = _declared_attributes(onto, obj)
    if declared is None:
        cache = onto.__dict__.get("_column_cache", {}).get(obj)
        declared = sorted(cache) if isinstance(cache, set) else []
    preferred = _preferred_labels(onto, obj)
    ordered = [c for c in preferred if c in declared] + sorted(
        c for c in declared if c not in preferred
    )
    identity = _identity_columns(onto, obj)
    pii = _pii_columns(onto, obj, con)
    types = {
        str(name).casefold(): (str(name), str(typ).upper())
        for name, typ, *_ in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()
    }
    dims = _Dims()
    for col in ordered:
        hit = types.get(col.casefold())
        if hit is None or col.casefold() in identity or hit[1] != "VARCHAR":
            continue
        name = hit[0]
        if name.casefold() in pii:
            dims.pii.append(name)
            continue
        # scripts/insights.py _label_attrs: a segment, not an annotation or a document.
        d, nn, total, maxlen = con.execute(
            f"SELECT COUNT(DISTINCT {_q(name)}), COUNT({_q(name)}), COUNT(*), "
            f"MAX(LENGTH({_q(name)})) FROM {rel}"
        ).fetchone()
        if not (2 <= int(d) <= 60):
            continue
        if int(total) == 0 or int(nn) * 2 <= int(total):
            continue
        if maxlen is not None and int(maxlen) > 64:
            continue
        dims.usable.append(name)
    return dims


def _candidate_objects(onto: Ontology, grain: str) -> list[tuple[str, dict[str, str] | None]]:
    """Copied from scripts/insights.py: every object, every role-playing link as a via."""
    out: list[tuple[str, dict[str, str] | None]] = []
    for obj in onto.objects:
        if obj == grain:
            continue
        into = [x for x in onto.links.values() if x.to_object == obj]
        by_from: dict[str, list[Any]] = {}
        for x in into:
            by_from.setdefault(x.from_object, []).append(x)
        role_playing = [links for links in by_from.values() if len(links) > 1]
        if role_playing:
            for links in role_playing:
                for link in links:
                    out.append((obj, {obj: link.name}))
        else:
            out.append((obj, None))
    return out


def _definition(onto: Ontology, m: Measure, source: str) -> dict[str, Any]:
    item_id: str | None = None
    version_id: str | None = None
    for meta in _item_meta(onto):
        payload = meta.get("payload")
        if meta.get("kind") == "measure" and isinstance(payload, dict) and str(
            payload.get("name") or ""
        ) == m.name:
            item_id = str(meta.get("id") or "") or None
            version_id = str(meta.get("version_id") or "") or None
    return {
        "measure": m.name,
        "grain": m.grain,
        "expression": m.expression,
        "additive": bool(m.additive),
        "description": m.description,
        "item_id": item_id,
        "version_id": version_id,
        "source": source,
    }


def _revoked(m: Measure) -> bool:
    """Fail closed: a store that cannot answer is treated as revoking."""
    try:
        from dms_executor.def_validate import _norm_text, _revoked_measures

        revoked = _revoked_measures()
        key = _norm_text(m.expression)
    except Exception:  # noqa: BLE001
        return True
    return bool(key) and key in revoked


def _read_marks(
    watermarks: Callable[..., Any], warehouse: Path, tables: Sequence[str]
) -> dict[str, Any]:
    try:
        got = watermarks(path=warehouse)
    except Exception:  # noqa: BLE001 - unknown is recorded as null, never as fresh
        got = {}
    if not isinstance(got, Mapping):
        got = {}
    # relation_tables() names are casefolded; a registry may store 'Orders'.
    folded: dict[str, Any] = {}
    for name, rec in got.items():
        folded.setdefault(str(name).casefold(), rec)
    out: dict[str, Any] = {}
    for t in tables:
        rec = got.get(t, folded.get(str(t).casefold()))
        out[t] = rec.get("extracted_at") if isinstance(rec, Mapping) else None
    return out


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------


@dataclass
class _Ctx:
    warehouse: Path
    submit: Callable[[str], Any]
    granted: set[str]
    space_id: str | None
    session_id: str | None
    watermarks: Callable[..., Any]
    onto: Ontology
    source: str
    refused: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    built: list[tuple[float, ProvenInsight]] = field(default_factory=list)
    compiles: int = 0


def _refuse(
    ctx: _Ctx | None,
    candidate: str,
    code: str,
    *,
    space_id: str | None = None,
    session_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = code.split(":", 1)[0]
    sid = ctx.space_id if ctx is not None else space_id
    sess = ctx.session_id if ctx is not None else session_id
    env = abstain.build_abstain(
        reason=code,
        stage=STAGE,
        question="insight:" + candidate,
        answer_id="ins_refused_" + hashlib.sha256(
            f"{candidate}|{code}".encode()
        ).hexdigest()[:16],
        text=REFUSALS[base],
        values=[],
        rows=[],
        sql_used=None,
        space_id=sid,
        session_id=sess,
    )
    entry: dict[str, Any] = {"candidate": candidate, "code": code, "envelope": env}
    if evidence:
        entry["evidence"] = dict(evidence)
    if ctx is not None:
        ctx.refused.append(entry)
    return entry


def prove_insights(
    warehouse: Path,
    *,
    submit: Callable[[str], Any],
    granted: Collection[str],
    space_id: str | None = None,
    session_id: str | None = None,
    top: int = 12,
    onto: Ontology | None = None,
    watermarks: Callable[..., Any] = bronze.lookup_ingest_watermarks,
) -> InsightRun:
    """Proven insights over the verified ontology, or the reason each was refused."""
    if not insight_proof_enabled():
        return InsightRun(status="off")
    if top < 1:
        # Zero insights asked for: nothing is opened, submitted or stated.
        return InsightRun(status="ok")
    from dms_executor.generative_ask import cached_verify_violations, load_verified_ontology

    loaded = load_verified_ontology(Path(warehouse), onto)
    if loaded is None or cached_verify_violations(loaded) != []:
        entry = _refuse(None, "ontology", "ontology_unverified",
                        space_id=space_id, session_id=session_id)
        return InsightRun(status="refused", refused=(entry,))
    source = "approved_store" if "_served_ids" in loaded.__dict__ else "demo_ontology"
    ctx = _Ctx(
        warehouse=Path(warehouse),
        submit=submit,
        granted={str(g) for g in granted if str(g).strip()},
        space_id=space_id,
        session_id=session_id,
        watermarks=watermarks,
        onto=loaded,
        source=source,
    )
    if not loaded.measures:
        _refuse(ctx, "ontology", "no_approved_measure")
        return InsightRun(status="refused", refused=tuple(ctx.refused))

    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(ctx.warehouse)
    try:
        dims_cache: dict[str, _Dims] = {}

        def dims_of(obj: str) -> _Dims:
            if obj not in dims_cache:
                dims_cache[obj] = _dimensions(con, loaded, obj)
            return dims_cache[obj]

        stopped = False
        for m in loaded.measures.values():
            if stopped:
                break
            stopped = _prove_measure(ctx, m, dims_of)
    finally:
        con.close()

    picked = _pick(ctx.built, top=top, n_measures=len(loaded.measures))
    status = "ok" if picked else "refused"
    return InsightRun(
        status=status,
        proven=tuple(picked),
        refused=tuple(ctx.refused),
        skipped=tuple(ctx.skipped),
    )


def _prove_measure(ctx: _Ctx, m: Measure, dims_of: Callable[[str], _Dims]) -> bool:
    """Prove every candidate of one measure. True when the run budget is spent."""
    onto = ctx.onto
    if _revoked(m):
        _refuse(ctx, m.name, "measure_revoked")
        return False
    if not m.additive:
        _refuse(ctx, m.name, "non_additive")
        return False
    if _VOLATILE.search(m.expression):
        _refuse(ctx, m.name, "volatile_measure")
        return False
    fact = onto.objects[m.grain]
    if not _granted_relation(fact.relation, ctx.granted):
        _refuse(ctx, m.name, "ungranted")
        return False

    candidates: list[tuple[str, str, dict[str, str] | None, bool]] = []
    for attr in dims_of(m.grain).pii:
        _refuse(ctx, f"{m.name} by {_dim_label(m.grain, attr, m.grain)}", "pii_dimension")
    for attr in dims_of(m.grain).usable:
        candidates.append((m.grain, attr, None, True))
    for obj, via in _candidate_objects(onto, m.grain):
        if not _granted_relation(onto.objects[obj].relation, ctx.granted):
            candidates.append((obj, onto.objects[obj].key[0], via, False))
            continue
        d = dims_of(obj)
        for attr in d.pii:
            _refuse(ctx, f"{m.name} by {_dim_label(obj, attr, m.grain)}", "pii_dimension")
        for attr in d.usable:
            candidates.append((obj, attr, via, True))

    for obj, attr, via, readable in candidates:
        ctx.compiles += 1
        if ctx.compiles > MAX_COMPILES:
            _refuse(ctx, "budget", "insights_budget")
            return True
        label = _dim_label(obj, attr, m.grain) if readable else obj
        candidate = f"{m.name} by {label}"
        got = onto.compile(m.name, group_by=[(obj, attr)], via=via)
        if isinstance(got, Refusal):
            if got.reason != "no_path":
                ctx.skipped.append({"candidate": candidate, "reason": got.reason})
            continue
        assert isinstance(got, CompiledQuery)
        read = _read_tables(got.sql)
        tables = sorted(read or ())
        if not readable or not read or not all(
            table_is_granted(t, ctx.granted) for t in tables
        ) or _statement_gap(got.sql, ctx.granted) is not None:
            _refuse(ctx, candidate, "ungranted")
            continue
        outcome = _prove_candidate(ctx, m, obj, attr, label, got, tables, candidate)
        if outcome == "measure_signed":
            break
    return False


def _prove_candidate(
    ctx: _Ctx,
    m: Measure,
    obj: str,
    attr: str,
    label: str,
    compiled: CompiledQuery,
    tables: list[str],
    candidate: str,
) -> str:
    from dms_executor.skills_quarantine import item_content_hash, item_result_hash
    from dms_executor.sql_vote import rows_equal

    pending: list[tuple[float, ProvenInsight]] = []
    executed_trace.begin()
    try:
        fact = ctx.onto.objects[m.grain]
        fact_rel = fact.relation
        raw_sql = f"SELECT {m.expression} AS raw_total FROM {fact_rel} f"
        parts_sql = _parts_sql(m, fact_rel, list(fact.key))
        sql = _wrapper_sql(compiled.sql, f"{obj}_{attr}", m.name)
        # Every statement this candidate submits passes main's statement check
        # first: the raw total, the parts count and the wrapper, not only the
        # compiled SQL the wrapper encloses.
        for stmt in (raw_sql, parts_sql, sql):
            if stmt is not None and _statement_gap(stmt, ctx.granted) is not None:
                _refuse(ctx, candidate, "ungranted")
                return "refused"
        before = _read_marks(ctx.watermarks, ctx.warehouse, tables)
        try:
            raw_rows = _submit_rows(ctx.submit, raw_sql)
            raw_cell = raw_rows[0].get("raw_total") if raw_rows else None
            if raw_cell is None or not _finite(raw_cell):
                _refuse(ctx, m.name, "empty_fact")
                return "measure_signed"
            raw_total = float(raw_cell)
            if raw_total <= 0:
                _refuse(ctx, m.name, "signed_measure")
                return "measure_signed"
            # A positive total and positive group sums can still net negative parts
            # (a return inside a territory). One part per fact key, none below zero.
            if parts_sql is None:
                _refuse(ctx, m.name, "signed_measure")
                return "measure_signed"
            parts_rows = _submit_rows(ctx.submit, parts_sql)
            neg_cell = parts_rows[0].get("negative_parts") if parts_rows else None
            if not _finite(neg_cell) or int(neg_cell) != 0:
                _refuse(ctx, m.name, "signed_measure")
                return "measure_signed"
            run1 = _project(_submit_rows(ctx.submit, sql))
            executed_at = _now_z()
            run2 = _project(_submit_rows(ctx.submit, sql))
            reexecuted_at = _now_z()
        except _Refused as r:
            _refuse(ctx, candidate, r.code, evidence=r.evidence)
            return "refused"
        after = _read_marks(ctx.watermarks, ctx.warehouse, tables)

        values = [r["value"] for r in run1]
        if any(not _finite(v) for v in values):
            # A group with no figure cannot add back to the total.
            _refuse(ctx, candidate, "not_conserved")
            return "refused"
        groups = len(run1)
        grouped_sum = sum(float(v) for v in values)
        cell_totals = {float(r["grouped_total"]) for r in run1 if _finite(r["grouped_total"])}
        tol = conservation_tolerance(groups)
        slack = tol + 1e-6
        conserves = (
            groups > 0
            and len(cell_totals) == 1
            and abs(grouped_sum - raw_total) <= slack
            and abs(next(iter(cell_totals)) - raw_total) <= slack
        )
        if not conserves:
            _refuse(ctx, candidate, "not_conserved")
            return "refused"
        if grouped_sum <= 0 or any(float(v) < 0 for v in values):
            _refuse(ctx, candidate, "signed_measure")
            return "refused"
        h1, h2 = item_result_hash(run1), item_result_hash(run2)
        o1, o2 = order_hash(run1), order_hash(run2)
        if h1 != h2 or o1 != o2 or not rows_equal(run1, run2):
            _refuse(ctx, candidate, "result_changed_between_runs",
                    evidence={"result_hash": h1, "reexec_result_hash": h2,
                              "order_hash": o1, "reexec_order_hash": o2})
            return "refused"
        # The templates read rows by position, so the order the SQL asked for is
        # checked here, on both runs, before any sentence is written.
        if not (_in_declared_order(run1) and _in_declared_order(run2)):
            _refuse(ctx, candidate, "result_out_of_order")
            return "refused"
        if before != after:
            _refuse(ctx, candidate, "source_refreshed_during_proof")
            return "refused"

        sql_hash = item_content_hash(sql)
        proof_base: dict[str, Any] = {
            "v": 1,
            "sql_hash": sql_hash,
            "raw_total_sql": raw_sql,
            "raw_total_sql_hash": item_content_hash(raw_sql),
            "result_hash": h1,
            "reexec_result_hash": h2,
            "order_hash": o2,
            "parts_sql_hash": item_content_hash(parts_sql),
            "negative_parts": 0,
            "raw_total": raw_total,
            "grouped_total": float(next(iter(cell_totals))),
            "tolerance": round(tol, 6),
            "conserves": True,
            "n_groups": groups,
            "executed_at": executed_at,
            "reexecuted_at": reexecuted_at,
            "watermarks": {t: _iso_z(after.get(t)) for t in tables},
            "definition": _definition(ctx.onto, m, ctx.source),
            "executions": [
                {"engine": "submit", "result_hash": h1, "row_count": len(run1)},
                {"engine": "submit", "result_hash": h2, "row_count": len(run2)},
            ],
            "ledger": None,
            "scope": PROOF_SCOPE,
        }
        found = _templates(ctx, m, label, run2, candidate)
        for kind, headline, surprise, k, n in found:
            made = _emit(ctx, m, kind, headline, k, n, label, sql, sql_hash,
                         run2, reexecuted_at, proof_base, candidate)
            if made is not None:
                pending.append((surprise, made))
        return "done"
    finally:
        trace = executed_trace.take()
        ctx.built.extend((s, replace(p, trace=trace)) for s, p in pending)


def _templates(
    ctx: _Ctx,
    m: Measure,
    label: str,
    rows: list[dict[str, Any]],
    candidate: str,
) -> list[tuple[str, str, float, int, int]]:
    """Copied thresholds of scripts/insights.py; the printed number is one cell."""
    labelled = [r for r in rows if r["label"] is not None]
    unlabelled = [r for r in rows if r["label"] is None]
    if len(labelled) < 2:
        _refuse(ctx, candidate, "too_few_groups")
        return []
    if len(labelled) > MAX_LABELS:
        _refuse(ctx, candidate, "too_many_groups")
        return []
    total = sum(float(r["value"]) for r in rows)
    n = len(labelled)
    k = max(1, min(3, n // 2))
    top_share = sum(float(r["value"]) for r in labelled[:k]) / total
    top1 = float(labelled[0]["value"]) / total
    top2 = float(labelled[1]["value"]) / total
    null_share = sum(float(r["value"]) for r in unlabelled) / total
    uniform_k = k / n
    tied = n > k and abs(float(labelled[k - 1]["value"]) - float(labelled[k]["value"])) < 1e-9
    out: list[tuple[str, str, float, int, int]] = []
    if n >= 4 and top_share >= 0.5 and top_share - uniform_k >= 0.2 and not tied:
        names = ", ".join(str(r["label"]) for r in labelled[:k])
        out.append((
            "concentration",
            f"{k} of {n} {label} values ({names}) carry "
            f"{format_share(labelled[k - 1]['cum_share_pct'])} pct of {m.name}",
            top_share - uniform_k, k, n,
        ))
    elif n >= 3 and top1 >= 0.4 and top1 > top2:
        out.append((
            "dominance",
            f"{labelled[0]['label']!s} alone is {format_share(labelled[0]['share_pct'])} "
            f"pct of {m.name} across {n} {label} values",
            top1 - 1.0 / n, 1, n,
        ))
    if unlabelled and null_share >= 0.05:
        out.append((
            "unknown_bucket",
            f"{format_share(unlabelled[0]['share_pct'])} pct of {m.name} lands on rows "
            f"with no {label} value",
            min(null_share, 0.3), k, n,
        ))
    if not out:
        ctx.skipped.append({"candidate": candidate, "reason": "no_template"})
    return out


def _emit(
    ctx: _Ctx,
    m: Measure,
    kind: str,
    headline: str,
    k: int,
    n: int,
    label: str,
    sql: str,
    sql_hash: str,
    rows: list[dict[str, Any]],
    as_of: str,
    proof_base: dict[str, Any],
    candidate: str,
) -> ProvenInsight | None:
    """Envelope, gates and proof for one insight. None when it was refused."""
    from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
    from dms_executor.route_stamp import ROUTE_COMPILE, stamp_route

    where = f"{candidate}:{kind}"
    if headline_numbers_backed(headline, rows, {k, n}):
        _refuse(ctx, where, "headline_unbacked")
        return None
    insight_id = insight_id_for(m.name, label, kind, sql_hash)
    assumptions = [m.description or f"measure {m.name}", CONSERVATION_ASSUMPTION]
    if ctx.source == "demo_ontology":
        assumptions.append(DEMO_ASSUMPTION)
    if any(stamp is None for stamp in proof_base["watermarks"].values()):
        assumptions.append(WATERMARK_UNKNOWN_ASSUMPTION)
    env = build_answer_envelope(
        answer_id=insight_id,
        text=headline,
        badge="L1_GOVERNED_METRIC" if ctx.source == "approved_store" else "L2_VALIDATED",
        sql_used=sql,
        rows=[dict(r) for r in rows],
        assumptions=assumptions,
        route="ontology_compile",
        as_of=as_of,
        space_id=ctx.space_id,
        session_id=ctx.session_id,
        # F32 derives competing scopes from the tables the SQL cites, for a ranking
        # whose source sheet the ask left open. Here the measure's declared grain
        # picked the fact and a verified many-to-one link picked the dimension, so
        # the cited tables are one scope, not two candidates. Stated, not derived.
        competing_scopes=[],
    )
    stamp_route(env, ROUTE_COMPILE)
    env = abstain.refuse_ungranted_answer(env, grants=ctx.granted)
    if env.get("abstained") is not False:
        code = "ungranted" if env.get("abstain_reason") == "ungranted" else (
            "envelope_invalid:demoted"
        )
        _refuse(ctx, where, code)
        return None
    try:
        assert_envelope_valid(env)
    except AssertionError as exc:
        msg = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(exc))[:80] or "assert"
        _refuse(ctx, where, f"envelope_invalid:{msg}")
        return None
    if env.get("rows") != rows or env.get("text") != headline:
        _refuse(ctx, where, "pii_dimension")
        return None
    proof = {**proof_base, "insight_id": insight_id, "kind": kind}
    try:
        assert_proof_shape(proof, onto=ctx.onto, sql=sql, rows=rows, label=label)
    except ProofShapeError:
        _refuse(ctx, where, "envelope_invalid:proof_shape")
        return None
    return ProvenInsight(
        insight_id=insight_id,
        kind=kind,
        measure=m.name,
        dimension=label,
        headline=headline,
        rows=[dict(r) for r in rows],
        k=k,
        n=n,
        sql=sql,
        envelope=env,
        proof=proof,
        trace=None,
    )


def _pick(
    built: list[tuple[float, ProvenInsight]], *, top: int, n_measures: int
) -> list[ProvenInsight]:
    """Dedupe copied from scripts/insights.py: one story per dimension and per shape.

    One addition: two columns that split some measure into the same figures are
    one dimension under two names (location_code and location.name on the demo
    lake). The first in candidate order, which tries the ontology's preferred
    grain labels first, names both, so the (grain, dimension, kind) key cannot
    be dodged by a twin column.
    """
    if top < 1:
        return []
    alias: dict[str, str] = {}
    first_by_shape: dict[tuple[str, str, tuple[Any, ...]], str] = {}
    for _surprise, p in built:  # candidate order
        shape = (p.measure, p.kind, tuple(r["value"] for r in p.rows))
        first = first_by_shape.setdefault(shape, p.dimension)
        if first != p.dimension:
            alias.setdefault(p.dimension, alias.get(first, first))
    ordered = sorted(built, key=lambda sp: -sp[0])
    seen: set[tuple[str, str, str]] = set()
    seen_shape: set[tuple[str, str, tuple[Any, ...]]] = set()
    per_measure: dict[str, int] = {}
    cap = max(2, top // max(1, n_measures) + 1)
    picked: list[ProvenInsight] = []
    for _surprise, p in ordered:
        grain = p.proof["definition"]["grain"]
        key = (grain, alias.get(p.dimension, p.dimension), p.kind)
        shape = (p.measure, p.kind, tuple(r["value"] for r in p.rows))
        if key in seen or shape in seen_shape or per_measure.get(p.measure, 0) >= cap:
            continue
        seen.add(key)
        seen_shape.add(shape)
        per_measure[p.measure] = per_measure.get(p.measure, 0) + 1
        picked.append(p)
        if len(picked) >= top:
            break
    return picked
