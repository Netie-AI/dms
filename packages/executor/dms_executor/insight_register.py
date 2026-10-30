"""INS-PROOF-03 - steward pins for proven insights (flag ``DMS_INSIGHT_PROOF``, default off).

A pin is the action-type equivalent of "a human steward confirmed this insight".
Nothing here pins, replays or applies on its own: every pin is a preview the
steward saw, then a confirm with a single-use token.

- ``preview_pin`` re-proves ONE candidate server side through the Space's Cortex
  submit (INS-PROOF-02's path). Client SQL or rows are never accepted. It mints
  a process-local, single-use token bound to the Space, the insight id, its SQL
  hash and its result hash, valid for ``TOKEN_TTL_S``. Tokens do not survive a
  server restart: after one they refuse as ``confirm_token_unknown`` (a
  documented ceiling, not a bug).
- ``confirm_pin`` spends the token, refuses a demo-ontology insight, re-proves
  and refuses a changed result as ``stale_preview``, refuses a second active pin
  for the same SQL, appends ONE ``insight.pinned`` pointer to the Cortex ledger
  (hashes and ids only) and only then writes the register row.
- ``replay_pin`` checks the approved definition first (no submit when it moved),
  then re-executes the PINNED SQL twice through submit and reports unchanged
  (L0), changed with a label-level drift block (L1), or a named refusal. Every
  verdict appends one ``insight.replayed`` pointer.
- ``revoke_pin`` appends ``insight.pin_revoked`` and then marks the row.

The register is ``main._proven_insights`` in the DMS warehouse DuckDB, a
DMS-owned table on the ``main._verified_queries`` precedent: created under
``lake_schema.catalog_lock``, written through ``connect_file(write=True)``, no
alembic migration. It is the only table this module writes; the ledger is
Cortex's, reached only through the injected ``ledger_append``.

Every function returns a plain dict: ``{"ok": True, ...}`` or
``{"ok": False, "code": <closed code>, ...}``. The route maps codes to HTTP.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
from dms_core.ask import ExecutedTrace

from dms_executor import abstain, bronze
from dms_executor import insight_proof as ip

TABLE = "main._proven_insights"
TOKEN_TTL_S = 600
_USED_MAX = 4096
_TOKENS_MAX = 4096
PIN_EVENT = "insight.pinned"
REPLAY_EVENT = "insight.replayed"
REVOKE_EVENT = "insight.pin_revoked"
SESSION = "insights"
VERDICTS = ("unchanged", "changed", "definition_changed", "refused")
DRIFT_REASONS = ("source_refreshed", "drift_without_source_change", "source_change_unknown")
REPLAY_SCOPE = (
    "the pinned SQL re-executed twice through Cortex submit with equal rows in the same "
    "order; grouped total conserved against a no-join raw total; result_hash compared "
    "with the hash the steward approved; drift is reported by label, never as a computed "
    "delta; contract 1.2.0 cannot read a ledger entry back"
)

#: Replay refusals this module adds to ``insight_proof.REFUSALS``.
TEXTS: dict[str, str] = {
    "insight_no_longer_holds": (
        "The approved insight no longer holds on the current data, so no figure is "
        "shown. Re-run insights and approve again if it still matters."
    ),
    "definition_source_changed": (
        "The ontology this insight was approved under is not the one in use now. "
        "Reconfirm it before it is shown."
    ),
}

_ASSET = re.compile(r"pin_[0-9a-f]{16}")
_INSIGHT = re.compile(r"ins_[0-9a-f]{16}")
_TOKEN_LOCK = threading.Lock()
#: Serialises the already-pinned check, the ledger append and the INSERT, so two
#: confirms of one insight cannot both pass the check (one 200, one 409).
_PIN_LOCK = threading.Lock()


def _clock() -> datetime:
    """Now, UTC. Monkeypatched by the token-expiry test."""
    return datetime.now(UTC)


def _z(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _marks(*, path: Path | None = None) -> Any:
    """Late-bound, so a test or a hot reload of the registry reader is honoured."""
    return bronze.lookup_ingest_watermarks(path=path)


def _off() -> dict[str, Any]:
    return {"ok": False, "code": "off"}


def _fail(code: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, **extra}


# --------------------------------------------------------------------------
# tokens (process-local, single use)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    space_id: str | None
    insight_id: str
    sql_hash: str
    result_hash: str
    source: str
    expires_at: datetime


_TOKENS: OrderedDict[str, _Token] = OrderedDict()
_USED: OrderedDict[str, None] = OrderedDict()


def _mint(tok: _Token) -> str:
    from dms_core.ids import mint_id

    token = mint_id("ipc")
    with _TOKEN_LOCK:
        _TOKENS[token] = tok
        while len(_TOKENS) > _TOKENS_MAX:
            _TOKENS.popitem(last=False)
    return token


def _spend(token: str) -> _Token | str:
    """The token's binding, spent; or the refusal code. Order: unknown, used, expired."""
    now = _clock()
    with _TOKEN_LOCK:
        if token in _USED:
            return "confirm_token_used"
        tok = _TOKENS.get(token)
        if tok is None:
            return "confirm_token_unknown"
        if now > tok.expires_at:
            return "confirm_token_expired"
        del _TOKENS[token]
        _USED[token] = None
        while len(_USED) > _USED_MAX:
            _USED.popitem(last=False)
    return tok


def token_space(token: str) -> str | None:
    """The Space a live token is bound to, for the gate's metadata. Never spends it."""
    with _TOKEN_LOCK:
        tok = _TOKENS.get(token)
    return tok.space_id if tok is not None else None


# --------------------------------------------------------------------------
# the register
# --------------------------------------------------------------------------


_COLUMNS = (
    "asset_id", "space_id", "insight_id", "measure", "dimension", "kind", "headline",
    "sql_text", "sql_hash", "expected_result_hash", "approved_rows_json",
    "definition_json", "watermarks_json", "approved_at", "approved_by",
    "ledger_entry_id", "revoked_at", "revoke_ledger_entry_id",
)


def _warehouse(executor: Any) -> Path:
    from dms_executor.demo_warehouse import warehouse_path

    return Path(getattr(executor, "_warehouse", None) or warehouse_path())


def _space_key(space_id: str | None) -> str:
    return (space_id or "").strip()


def _ensure(con: duckdb.DuckDBPyConnection) -> None:
    from dms_executor.lake_schema import catalog_lock

    with catalog_lock:
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
              asset_id VARCHAR PRIMARY KEY,
              space_id VARCHAR NOT NULL,
              insight_id VARCHAR NOT NULL,
              measure VARCHAR NOT NULL,
              dimension VARCHAR NOT NULL,
              kind VARCHAR NOT NULL,
              headline VARCHAR NOT NULL,
              sql_text VARCHAR NOT NULL,
              sql_hash VARCHAR NOT NULL,
              expected_result_hash VARCHAR NOT NULL,
              approved_rows_json VARCHAR NOT NULL,
              definition_json VARCHAR NOT NULL,
              watermarks_json VARCHAR NOT NULL,
              approved_at TIMESTAMPTZ NOT NULL,
              approved_by VARCHAR NOT NULL,
              ledger_entry_id VARCHAR NOT NULL,
              revoked_at TIMESTAMPTZ,
              revoke_ledger_entry_id VARCHAR
            )
            """
        )


def _read(wh: Path, where: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(wh)
    try:
        try:
            raw = con.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM {TABLE} WHERE {where} "
                "ORDER BY approved_at, asset_id",
                list(params),
            ).fetchall()
        except duckdb.CatalogException:
            return []  # nothing pinned yet: lookup never creates the table
    finally:
        con.close()
    return [dict(zip(_COLUMNS, row, strict=True)) for row in raw]


def _load_pin(wh: Path, asset_id: str) -> dict[str, Any] | None:
    if not _ASSET.fullmatch(asset_id or ""):
        return None
    rows = _read(wh, "asset_id = ?", [asset_id])
    return rows[0] if rows else None


def _active_pin(wh: Path, space: str, sql_hash: str) -> str | None:
    rows = _read(wh, "space_id = ? AND sql_hash = ? AND revoked_at IS NULL", [space, sql_hash])
    return str(rows[0]["asset_id"]) if rows else None


def _insert_pin(wh: Path, row: Mapping[str, Any]) -> None:
    from dms_executor.demo_warehouse import connect_file

    cols = [c for c in _COLUMNS if c in row]
    con = connect_file(wh, write=True)
    try:
        _ensure(con)
        con.execute(
            f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )
    finally:
        con.close()


def _mark_revoked(wh: Path, asset_id: str, at: datetime, entry_id: str) -> None:
    from dms_executor.demo_warehouse import connect_file

    con = connect_file(wh, write=True)
    try:
        _ensure(con)
        con.execute(
            f"UPDATE {TABLE} SET revoked_at = ?, revoke_ledger_entry_id = ? "
            "WHERE asset_id = ? AND revoked_at IS NULL",
            [at, entry_id, asset_id],
        )
    finally:
        con.close()


def _stamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        at = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return _z(at)
    return ip._iso_z(value)


def _public(pin: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": pin["asset_id"],
        "space_id": pin["space_id"] or None,
        "insight_id": pin["insight_id"],
        "measure": pin["measure"],
        "dimension": pin["dimension"],
        "kind": pin["kind"],
        "headline": pin["headline"],
        "sql_hash": pin["sql_hash"],
        "expected_result_hash": pin["expected_result_hash"],
        "approved_at": _stamp(pin["approved_at"]),
        "approved_by": pin["approved_by"],
        "ledger_entry_id": pin["ledger_entry_id"],
    }


def pin_space(executor: Any, asset_id: str) -> str | None:
    """The Space a pin belongs to, for the gate's metadata. A read, never a write."""
    if not ip.insight_proof_enabled():
        return None
    pin = _load_pin(_warehouse(executor), asset_id)
    return (pin["space_id"] or None) if pin is not None else None


def list_pinned(executor: Any, *, space_id: str | None) -> list[dict[str, Any]]:
    """Active pins of one Space (``None``: the personal context), oldest first."""
    if not ip.insight_proof_enabled():
        return []
    rows = _read(_warehouse(executor), "space_id = ? AND revoked_at IS NULL",
                 [_space_key(space_id)])
    return [_public(r) for r in rows]


# --------------------------------------------------------------------------
# ledger receipts
# --------------------------------------------------------------------------


def _append(
    ledger_append: Callable[..., Any], event_type: str, payload: dict[str, Any],
    actor: str | None,
) -> tuple[str, int | None] | str:
    """``(entry_id, seq)``, or the refusal code. The verified-query receipt rule."""
    try:
        led = ledger_append(event_type=event_type, payload=payload, actor=actor)
    except Exception:  # noqa: BLE001 - no pin and no replay is served without an entry
        return "ledger_append_failed"
    entry_id = getattr(led, "entry_id", None) if led is not None else None
    led_hash = getattr(led, "hash", None) if led is not None else None
    if (
        not (isinstance(entry_id, str) and entry_id.strip())
        or not (isinstance(led_hash, str) and led_hash.strip())
        or led_hash.strip() == entry_id.strip()
        or not ip._IDENT.fullmatch(entry_id.strip())
    ):
        return "ledger_receipt_invalid"
    raw_seq = getattr(led, "seq", None)
    seq = raw_seq if type(raw_seq) is int and raw_seq >= 0 else None
    return entry_id.strip(), seq


def _served_trace(p_trace: Any, sql: str, entry_id: str, seq: int | None) -> ExecutedTrace:
    trace = p_trace if isinstance(p_trace, ExecutedTrace) else ExecutedTrace()
    hits = [(s, n) for s, n in trace.statements if s == sql]
    return ExecutedTrace(statements=tuple(hits[-1:]), ledger=((entry_id, seq),))


# --------------------------------------------------------------------------
# preview and confirm
# --------------------------------------------------------------------------


def _reprove(executor: Any, space_id: str | None, insight_id: str) -> ip.InsightRun:
    return ip.space_run(executor, space_id=space_id, session_id=SESSION, watermarks=_marks,
                        want=insight_id)


def preview_pin(executor: Any, *, space_id: str | None, insight_id: str) -> dict[str, Any]:
    """Re-prove one insight and mint the token that may pin exactly what was shown."""
    if not ip.insight_proof_enabled():
        return _off()
    if not _INSIGHT.fullmatch(insight_id or ""):
        return _fail("insight_not_found")
    run = _reprove(executor, space_id, insight_id)
    if not run.proven:
        return _fail("insight_not_found")
    p = run.proven[0]
    expires = _clock() + timedelta(seconds=TOKEN_TTL_S)
    token = _mint(_Token(
        space_id=space_id,
        insight_id=p.insight_id,
        sql_hash=str(p.proof["sql_hash"]),
        result_hash=str(p.proof["result_hash"]),
        source=str(p.proof["definition"]["source"]),
        expires_at=expires,
    ))
    return {"ok": True, "envelope": p.envelope, "proof": p.proof, "token": token,
            "expires_at": _z(expires)}


def confirm_pin(
    executor: Any, *, token: str, actor: str, ledger_append: Callable[..., Any]
) -> dict[str, Any]:
    """Spend the token, re-prove, then one ledger pointer, then one register row."""
    if not ip.insight_proof_enabled():
        return _off()
    from dms_executor.skills_quarantine import reject_scored_write

    tok = _spend(str(token or ""))
    if isinstance(tok, str):
        return _fail(tok)
    if tok.source != "approved_store":
        return _fail("measure_not_approved")
    run = _reprove(executor, tok.space_id, tok.insight_id)
    if not run.proven:
        return _fail("stale_preview", shown_result_hash=tok.result_hash,
                     current_result_hash=None)
    p = run.proven[0]
    current = str(p.proof["result_hash"])
    if current != tok.result_hash or p.proof["sql_hash"] != tok.sql_hash:
        return _fail("stale_preview", shown_result_hash=tok.result_hash,
                     current_result_hash=current)
    if p.proof["definition"]["source"] != "approved_store":
        return _fail("measure_not_approved")
    wh = _warehouse(executor)
    space = _space_key(tok.space_id)
    with _PIN_LOCK:
        existing = _active_pin(wh, space, tok.sql_hash)
        if existing is not None:
            return _fail("already_pinned", asset_id=existing)
        try:
            reject_scored_write({"sql": p.sql}, warehouse=wh)
        except ValueError:
            return _fail("scored_write_blocked")
        asset_id = "pin_" + uuid.uuid4().hex[:16]
        got = _append(
            ledger_append,
            PIN_EVENT,
            {
                "asset_id": asset_id,
                "insight_id": p.insight_id,
                "sql_hash": tok.sql_hash,
                "expected_result_hash": current,
                "space_id": tok.space_id,
                "approved_by": actor,
            },
            actor,
        )
        if isinstance(got, str):
            return _fail(got)
        entry_id, seq = got
        approved_at = _clock()
        d = p.proof["definition"]
        try:
            _insert_pin(wh, {
                "asset_id": asset_id,
                "space_id": space,
                "insight_id": p.insight_id,
                "measure": p.measure,
                "dimension": p.dimension,
                "kind": p.kind,
                "headline": p.headline,
                "sql_text": p.sql,
                "sql_hash": tok.sql_hash,
                "expected_result_hash": current,
                "approved_rows_json": json.dumps(p.rows),
                "definition_json": json.dumps({
                    k: d[k] for k in ("measure", "grain", "expression", "item_id",
                                      "version_id", "source")
                }),
                "watermarks_json": json.dumps(p.proof["watermarks"]),
                "approved_at": approved_at,
                "approved_by": actor,
                "ledger_entry_id": entry_id,
            })
        except Exception:  # noqa: BLE001 - the entry exists; say which one has no row
            return _fail("pin_commit_failed", orphan_ledger_entry=entry_id)
    env = dict(p.envelope)
    env["audit_id"] = entry_id
    return {
        "ok": True,
        "asset_id": asset_id,
        "insight_id": p.insight_id,
        "approved_at": _z(approved_at),
        "approved_by": actor,
        "expected_result_hash": current,
        "ledger_entry_id": entry_id,
        "envelope": env,
        "trace": _served_trace(p.trace, p.sql, entry_id, seq),
    }


# --------------------------------------------------------------------------
# replay
# --------------------------------------------------------------------------


def _labels(rows: Sequence[Mapping[str, Any]]) -> dict[Any, Any]:
    return {r.get("label"): r.get("value") for r in rows}


def _drift_reason(approved: Mapping[str, Any], current: Mapping[str, Any]) -> str:
    pairs = [(approved.get(t), current.get(t)) for t in sorted(set(approved) | set(current))]
    if any(a is not None and c is not None and a != c for a, c in pairs):
        return "source_refreshed"
    if pairs and all(a is not None and c is not None for a, c in pairs):
        return "drift_without_source_change"
    return "source_change_unknown"


def _drift(approved_rows: list[dict[str, Any]], rows: list[dict[str, Any]],
           approved_hash: str, current_hash: str, reason: str) -> dict[str, Any]:
    old, new = _labels(approved_rows), _labels(rows)

    def order(xs: list[Any]) -> list[Any]:
        return sorted(xs, key=lambda x: (x is None, str(x)))

    return {
        "approved_result_hash": approved_hash,
        "current_result_hash": current_hash,
        "labels_added": order([lab for lab in new if lab not in old]),
        "labels_removed": order([lab for lab in old if lab not in new]),
        "labels_changed": [lab for lab in new if lab in old and old[lab] != new[lab]],
        "reason": reason,
    }


def _refusal_text(code: str, measure: str) -> str:
    base = code.split(":", 1)[0]
    if base == "definition_changed":
        return (
            f"The approved definition of {measure} changed since this insight was approved; "
            "reconfirm it before it is shown."
        )
    return TEXTS.get(base) or ip.REFUSALS.get(base) or ip.REFUSALS["envelope_invalid"]


def _replay_refusal(pin: Mapping[str, Any], code: str) -> dict[str, Any]:
    from dms_executor.envelope import assert_envelope_valid

    env = abstain.build_abstain(
        reason=code,
        stage=ip.STAGE,
        question="insight:" + str(pin["insight_id"]),
        answer_id="ans_insproof_replay_" + hashlib.sha256(
            f"{pin['asset_id']}|{code}".encode()
        ).hexdigest()[:16],
        text=_refusal_text(code, str(pin["measure"])),
        values=[],
        rows=[],
        sql_used=None,
        space_id=pin["space_id"] or None,
        session_id=SESSION,
    )
    assert_envelope_valid(env)
    return env


@dataclass
class _Replayed:
    verdict: str
    envelope: dict[str, Any]
    proof: dict[str, Any]
    result_hash: str | None
    code: str | None = None
    trace: ExecutedTrace | None = None


def _base_proof(pin: Mapping[str, Any], verdict: str) -> dict[str, Any]:
    return {
        "v": 1,
        "asset_id": pin["asset_id"],
        "insight_id": pin["insight_id"],
        "kind": pin["kind"],
        "verdict": verdict,
        "code": None,
        "approved_at": _stamp(pin["approved_at"]),
        "expected_result_hash": pin["expected_result_hash"],
        "sql_hash": pin["sql_hash"],
        "result_hash": None,
        "drift": None,
        "ledger": None,
        "scope": REPLAY_SCOPE,
    }


def _refused(pin: Mapping[str, Any], code: str, verdict: str = "refused",
             trace: ExecutedTrace | None = None) -> _Replayed:
    proof = {**_base_proof(pin, verdict), "code": code}
    return _Replayed(verdict, _replay_refusal(pin, code), proof, None, code, trace)


def check_replay_proof(proof: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` unless a served replay proof agrees with itself."""
    if proof.get("verdict") not in ("unchanged", "changed"):
        raise ValueError("not a served verdict")
    if proof.get("conserves") is not True:
        raise ValueError("does not conserve")
    if abs(float(proof["raw_total"]) - float(proof["grouped_total"])) > float(
        proof["tolerance"]
    ) + 1e-6:
        raise ValueError("grouped total outside tolerance")
    if proof["result_hash"] != proof["reexec_result_hash"]:
        raise ValueError("the two runs differ")
    if [e["result_hash"] for e in proof["executions"]] != [proof["result_hash"]] * 2:
        raise ValueError("executions disagree")
    if any(e["row_count"] != proof["n_groups"] for e in proof["executions"]):
        raise ValueError("row counts disagree")
    same = proof["result_hash"] == proof["expected_result_hash"]
    if (proof["verdict"] == "unchanged") != same:
        raise ValueError("verdict disagrees with the hashes")
    drift = proof.get("drift")
    if same and drift is not None:
        raise ValueError("drift on an unchanged result")
    if not same and (
        not isinstance(drift, Mapping)
        or drift.get("current_result_hash") != proof["result_hash"]
        or drift.get("approved_result_hash") != proof["expected_result_hash"]
        or drift.get("reason") not in DRIFT_REASONS
    ):
        raise ValueError("drift block disagrees with the hashes")


def _execute(executor: Any, pin: Mapping[str, Any]) -> _Replayed:
    """Definition check (no submit), then the pinned SQL twice through Cortex submit."""
    from dms_executor.envelope import assert_envelope_valid, build_answer_envelope
    from dms_executor.generative_ask import cached_verify_violations, load_verified_ontology
    from dms_executor.ontology import table_is_granted
    from dms_executor.route_stamp import ROUTE_VERIFIED, stamp_route
    from dms_executor.skills_quarantine import item_content_hash, item_result_hash
    from dms_executor.sql_vote import rows_equal

    wh = _warehouse(executor)
    space_id = pin["space_id"] or None
    definition = json.loads(pin["definition_json"])
    measure = str(pin["measure"])

    loaded = load_verified_ontology(wh)
    if loaded is None or cached_verify_violations(loaded) != []:
        return _refused(pin, "ontology_unverified")
    if "_served_ids" not in loaded.__dict__:
        return _refused(pin, "definition_source_changed", "definition_changed")
    m = loaded.measures.get(measure)
    if (
        m is None
        or m.expression != definition.get("expression")
        or m.grain != definition.get("grain")
        or ip._revoked(m)
    ):
        return _refused(pin, "definition_changed", "definition_changed")

    sql = str(pin["sql_text"])
    if item_content_hash(sql) != pin["sql_hash"]:
        return _refused(pin, "envelope_invalid:sql_hash")
    approved_marks = json.loads(pin["watermarks_json"])
    tables = sorted(approved_marks)
    access = ip.space_access(executor, space_id=space_id, session_id=SESSION)
    fact = loaded.objects[m.grain]
    read = ip._read_tables(fact.relation) or frozenset()
    if not tables or not all(table_is_granted(t, set(access.granted))
                             for t in (*tables, *read)):
        policy = set(access.policy)
        code = "ungranted:row_policy" if policy and any(
            table_is_granted(t, policy) for t in (*tables, *read)) else "ungranted"
        return _refused(pin, code)

    from dms_executor import executed_trace

    raw_sql = f"SELECT {m.expression} AS raw_total FROM {fact.relation} f"
    parts_sql = ip._parts_sql(m, fact.relation, list(fact.key))
    executed_trace.begin()
    try:
        before = ip._read_marks(_marks, wh, tables)
        try:
            raw_rows = ip._submit_rows(access.submit, raw_sql)
            raw_cell = raw_rows[0].get("raw_total") if raw_rows else None
            if raw_cell is None or not ip._finite(raw_cell):
                return _refused(pin, "empty_fact")
            raw_total = float(raw_cell)
            if raw_total <= 0 or parts_sql is None:
                return _refused(pin, "signed_measure")
            parts = ip._submit_rows(access.submit, parts_sql)
            neg = parts[0].get("negative_parts") if parts else None
            if not ip._finite(neg) or int(neg) != 0:
                return _refused(pin, "signed_measure")
            run1 = ip._project(ip._submit_rows(access.submit, sql))
            executed_at = ip._now_z()
            run2 = ip._project(ip._submit_rows(access.submit, sql))
            reexecuted_at = ip._now_z()
        except ip._Refused as r:
            return _refused(pin, r.code)
        after = ip._read_marks(_marks, wh, tables)
    finally:
        trace = executed_trace.take()

    values = [r["value"] for r in run1]
    if not values or any(not ip._finite(v) for v in values):
        return _refused(pin, "not_conserved")
    groups = len(run1)
    grouped_sum = sum(float(v) for v in values)
    totals = {float(r["grouped_total"]) for r in run1 if ip._finite(r["grouped_total"])}
    tol = 0.005 * groups + 0.005
    slack = tol + 1e-6
    if not (len(totals) == 1 and abs(grouped_sum - raw_total) <= slack
            and abs(next(iter(totals)) - raw_total) <= slack):
        return _refused(pin, "not_conserved")
    if any(float(v) < 0 for v in values):
        return _refused(pin, "signed_measure")
    h1, h2 = item_result_hash(run1), item_result_hash(run2)
    o1, o2 = ip.order_hash(run1), ip.order_hash(run2)
    if h1 != h2 or o1 != o2 or not rows_equal(run1, run2):
        return _refused(pin, "result_changed_between_runs")
    if not (ip._in_declared_order(run1) and ip._in_declared_order(run2)):
        return _refused(pin, "result_out_of_order")
    if before != after:
        return _refused(pin, "source_refreshed_during_proof")

    ctx = ip._Ctx(warehouse=wh, submit=access.submit, granted=set(access.granted),
                  space_id=space_id, session_id=SESSION, watermarks=_marks, onto=loaded,
                  source="approved_store")
    candidate = f"{measure} by {pin['dimension']}"
    found = [f for f in ip._templates(ctx, m, str(pin["dimension"]), run2, candidate)
             if f[0] == pin["kind"]]
    if not found:
        code = ctx.refused[0]["code"] if ctx.refused else "insight_no_longer_holds"
        return _refused(pin, code)
    _kind, headline, _surprise, k, n = found[0]
    if ip.headline_numbers_backed(headline, run2, {k, n}):
        return _refused(pin, "headline_unbacked")

    current_marks = {t: ip._iso_z(after.get(t)) for t in tables}
    approved_on = (_stamp(pin["approved_at"]) or "")[:10]
    unchanged = h2 == pin["expected_result_hash"]
    assumptions = [m.description or f"measure {m.name}", ip.CONSERVATION_ASSUMPTION]
    drift: dict[str, Any] | None = None
    if unchanged:
        text = f"Unchanged since steward approval on {approved_on}: {headline}"
        badge = "L0_CERTIFIED"
    else:
        text = headline
        badge = "L1_GOVERNED_METRIC"
        assumptions.append(f"Figures changed since steward approval on {approved_on}")
        drift = _drift(json.loads(pin["approved_rows_json"]), run2,
                       str(pin["expected_result_hash"]), h2,
                       _drift_reason(approved_marks, current_marks))
    if any(stamp is None for stamp in current_marks.values()):
        assumptions.append(ip.WATERMARK_UNKNOWN_ASSUMPTION)
    env = build_answer_envelope(
        answer_id=str(pin["insight_id"]),
        text=text,
        badge=badge,
        sql_used=sql,
        rows=[dict(r) for r in run2],
        assumptions=assumptions,
        route="verified_query",
        as_of=reexecuted_at,
        space_id=space_id,
        session_id=SESSION,
        competing_scopes=[],
    )
    stamp_route(env, ROUTE_VERIFIED)
    env = abstain.refuse_ungranted_answer(env, grants=set(access.granted))
    if env.get("abstained") is not False:
        return _refused(pin, "ungranted", trace=trace)
    try:
        assert_envelope_valid(env)
    except AssertionError as exc:
        msg = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(exc))[:80] or "assert"
        return _refused(pin, f"envelope_invalid:{msg}", trace=trace)
    if env.get("rows") != run2 or env.get("text") != text:
        return _refused(pin, "pii_dimension", trace=trace)
    verdict = "unchanged" if unchanged else "changed"
    proof = {
        **_base_proof(pin, verdict),
        "raw_total_sql_hash": item_content_hash(raw_sql),
        "parts_sql_hash": item_content_hash(parts_sql),
        "result_hash": h1,
        "reexec_result_hash": h2,
        "order_hash": o2,
        "raw_total": raw_total,
        "grouped_total": float(next(iter(totals))),
        "tolerance": round(tol, 6),
        "conserves": True,
        "n_groups": groups,
        "executed_at": executed_at,
        "reexecuted_at": reexecuted_at,
        "watermarks": current_marks,
        "approved_watermarks": approved_marks,
        "definition": ip._definition(loaded, m, "approved_store"),
        "executions": [
            {"engine": "submit", "result_hash": h1, "row_count": len(run1)},
            {"engine": "submit", "result_hash": h2, "row_count": len(run2)},
        ],
        "drift": drift,
    }
    try:
        check_replay_proof(proof)
    except (ValueError, KeyError, TypeError):
        return _refused(pin, "envelope_invalid:proof_shape", trace=trace)
    return _Replayed(verdict, env, proof, h2, trace=trace)


def replay_pin(
    executor: Any, asset_id: str, *, ledger_append: Callable[..., Any]
) -> dict[str, Any]:
    """One replay of an active pin: a verdict, its envelope and proof, one ledger pointer."""
    if not ip.insight_proof_enabled():
        return _off()
    from dms_executor.envelope import assert_envelope_valid

    pin = _load_pin(_warehouse(executor), asset_id)
    if pin is None or pin["revoked_at"] is not None:
        return _fail("pin_not_found")
    done = _execute(executor, pin)
    got = _append(
        ledger_append,
        REPLAY_EVENT,
        {
            "asset_id": pin["asset_id"],
            "sql_hash": pin["sql_hash"],
            "result_hash": done.result_hash,
            "verdict": done.verdict,
        },
        None,
    )
    if isinstance(got, str):
        return _fail(got)
    entry_id, seq = got
    env = dict(done.envelope)
    env["audit_id"] = entry_id
    assert_envelope_valid(env)
    proof = {**done.proof, "ledger": {"event_type": REPLAY_EVENT, "entry_id": entry_id,
                                      "seq": seq}}
    trace = done.trace or ExecutedTrace()
    statements = tuple(s for s in trace.statements if s[0] == pin["sql_text"])[-1:]
    if env.get("abstained") is not False:
        statements = ()
    return {
        "ok": True,
        "asset_id": pin["asset_id"],
        "insight_id": pin["insight_id"],
        "verdict": done.verdict,
        "code": done.code,
        "envelope": env,
        "proof": proof,
        "trace": ExecutedTrace(statements=statements, ledger=((entry_id, seq),)),
    }


# --------------------------------------------------------------------------
# revoke
# --------------------------------------------------------------------------


def revoke_pin(
    executor: Any, asset_id: str, *, actor: str, ledger_append: Callable[..., Any]
) -> dict[str, Any]:
    """Ledger ``insight.pin_revoked`` first, then the row's ``revoked_at``."""
    if not ip.insight_proof_enabled():
        return _off()
    wh = _warehouse(executor)
    with _PIN_LOCK:
        pin = _load_pin(wh, asset_id)
        if pin is None:
            return _fail("pin_not_found")
        if pin["revoked_at"] is not None:
            return _fail("not_pinned")
        got = _append(
            ledger_append,
            REVOKE_EVENT,
            {"asset_id": pin["asset_id"], "sql_hash": pin["sql_hash"],
             "space_id": pin["space_id"] or None, "revoked_by": actor},
            actor,
        )
        if isinstance(got, str):
            return _fail(got)
        entry_id, _seq = got
        at = _clock()
        try:
            _mark_revoked(wh, str(pin["asset_id"]), at, entry_id)
        except Exception:  # noqa: BLE001 - the entry exists; say which one has no row
            return _fail("revoke_commit_failed", orphan_ledger_entry=entry_id)
    return {"ok": True, "asset_id": pin["asset_id"], "revoked_at": _z(at),
            "revoke_ledger_entry_id": entry_id}
