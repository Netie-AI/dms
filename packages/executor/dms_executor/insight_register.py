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
  verdict appends one ``insight.replayed`` pointer. A Cortex failure is not a
  verdict: it is ``cortex_unavailable`` / ``cortex_submit_failed`` (a 503) with
  no append, as at preview and confirm.
- ``revoke_pin`` appends ``insight.pin_revoked`` and then marks the row; the
  stored headline and approved rows (they carry labels) are cleared with it.

Replay re-checks governance before any submit, not only the measure: every
ontology item the pinned SQL was compiled from (the measure, the objects and
links on its path) must still be served at the version the steward approved,
the SQL must recompile byte-identically, and the INS-PROOF-01 PII gate is run on
the pinned dimension. Any miss is ``definition_changed`` with zero submits, and
GET /pins withholds the stored headline of a pin whose dimension is now PII.

The register is ``main._proven_insights`` in the DMS warehouse DuckDB, a
DMS-owned table on the ``main._verified_queries`` precedent: created under
``lake_schema.catalog_lock``, written through ``connect_file(write=True)``, no
alembic migration. Beside it, ``main._proven_insight_receipts`` holds one row
per ledger receipt a pin, replay or revoke accepted (entry id and seq, both
unique), so one receipt can never back two of them: a repeat is refused
``ledger_receipt_invalid:repeated``, the INS-PROOF-02 rule for ``/run`` applied
across the pin lifecycle. Those two tables are the only ones this module
writes; the ledger is Cortex's, reached only through the injected
``ledger_append``.

A register write that raised is never taken as failed on its word: the row is
re-read and the response reports what the register actually holds.

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
RECEIPTS = "main._proven_insight_receipts"
REPEATED = "ledger_receipt_invalid:repeated"
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
    "pii_dimension": (
        "A column this approved insight groups by is now marked as personal data, so "
        "nothing of it is shown. Revoke the pin."
    ),
}

#: Re-prove refusals that are the engine, not the data: a 503, and the token is
#: given back so the steward can confirm once Cortex answers again.
INFRA_CODES = ("cortex_unavailable", "cortex_submit_failed")

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


def _give_back(token: str, tok: _Token) -> None:
    """Undo ``_spend`` after an infrastructure failure. The expiry is unchanged."""
    with _TOKEN_LOCK:
        _USED.pop(token, None)
        _TOKENS[token] = tok


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
    "ledger_entry_id", "revoked_at", "revoke_ledger_entry_id", "active_key",
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
              revoke_ledger_entry_id VARCHAR,
              active_key VARCHAR
            )
            """
        )
        # One active pin per (space_id, sql_hash) is a database rule, not only
        # _PIN_LOCK: active_key is set while active and NULL once revoked, and a
        # UNIQUE index admits any number of NULLs. A table from before the column
        # gains it here.
        con.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS active_key VARCHAR")
        con.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS _proven_insights_active ON {TABLE} (active_key)"
        )
        # One row per accepted receipt. Two appends are never one chain position,
        # so an entry id or a non-null seq seen twice is a replayed receipt.
        con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {RECEIPTS} (
              entry_id VARCHAR PRIMARY KEY,
              seq BIGINT,
              event_type VARCHAR NOT NULL,
              asset_id VARCHAR NOT NULL,
              recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        con.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS _proven_insight_receipts_seq ON {RECEIPTS} (seq)"
        )


@dataclass(frozen=True)
class _Receipt:
    entry_id: str
    seq: int | None
    event_type: str
    asset_id: str


def _receipt_used(con: duckdb.DuckDBPyConnection, rc: _Receipt) -> bool:
    """Has this receipt already backed a pin, a replay or a revoke?

    The receipts table, and the register's own pointers (a row written before the
    receipts table existed has no receipt row).
    """
    if rc.seq is None:
        hit = con.execute(
            f"SELECT 1 FROM {RECEIPTS} WHERE entry_id = ? LIMIT 1", [rc.entry_id]
        ).fetchone()
    else:
        hit = con.execute(
            f"SELECT 1 FROM {RECEIPTS} WHERE entry_id = ? OR seq = ? LIMIT 1",
            [rc.entry_id, rc.seq],
        ).fetchone()
    if hit is not None:
        return True
    return con.execute(
        f"SELECT 1 FROM {TABLE} WHERE ledger_entry_id = ? OR revoke_ledger_entry_id = ? LIMIT 1",
        [rc.entry_id, rc.entry_id],
    ).fetchone() is not None


def _write_with_receipt(
    wh: Path, rc: _Receipt, write: Callable[[duckdb.DuckDBPyConnection], None] | None
) -> str | None:
    """Claim the receipt, then the register write, on one write connection.

    ``None``: both returned. ``"repeated"``: the receipt already backs something;
    nothing was written. ``"conflict"``: the receipt is claimed and the register
    write hit its UNIQUE rule. ``"unknown"``: something raised, the outcome is not
    known, and the caller must re-read the register before it answers.

    The receipt is claimed in its own statement before the write, so a receipt
    whose register write then fails stays spent: it cannot back a retry.
    """
    from dms_executor.demo_warehouse import connect_file

    try:
        con = connect_file(wh, write=True)
    except Exception:  # noqa: BLE001 - nothing was written, but say "unknown", never "failed"
        return "unknown"
    outcome: str | None
    try:
        try:
            _ensure(con)
            if _receipt_used(con, rc):
                outcome = "repeated"
            else:
                con.execute(
                    f"INSERT INTO {RECEIPTS} (entry_id, seq, event_type, asset_id, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [rc.entry_id, rc.seq, rc.event_type, rc.asset_id, _clock()],
                )
                outcome = None
        except duckdb.ConstraintException:
            outcome = "repeated"  # another process claimed it between check and insert
        if outcome is None and write is not None:
            try:
                write(con)
            except duckdb.ConstraintException:
                outcome = "conflict"
    except Exception:  # noqa: BLE001 - the caller re-reads; it never guesses
        outcome = "unknown"
    try:
        con.close()
    except Exception:  # noqa: BLE001 - the statements may have landed: re-read
        if outcome is None:
            outcome = "unknown"
    return outcome


def _receipt_recorded(wh: Path, rc: _Receipt) -> bool:
    """After an ``unknown``: is this receipt's row in the receipts table?"""
    from dms_executor.demo_warehouse import connect_serving

    con = connect_serving(wh)
    try:
        try:
            row = con.execute(
                f"SELECT event_type, asset_id FROM {RECEIPTS} WHERE entry_id = ?",
                [rc.entry_id],
            ).fetchone()
        except duckdb.CatalogException:
            return False
    finally:
        con.close()
    return row is not None and tuple(row) == (rc.event_type, rc.asset_id)


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


def _active_key(space: str, sql_hash: str) -> str:
    return f"{space}|{sql_hash}"


def _active_pin(wh: Path, space: str, sql_hash: str) -> str | None:
    rows = _read(wh, "space_id = ? AND sql_hash = ? AND revoked_at IS NULL", [space, sql_hash])
    return str(rows[0]["asset_id"]) if rows else None


def _insert_pin(wh: Path, row: Mapping[str, Any], rc: _Receipt) -> str | None:
    """Claim the pin's receipt, then INSERT its row. ``_write_with_receipt`` outcomes."""
    cols = [c for c in _COLUMNS if c in row]

    def write(con: duckdb.DuckDBPyConnection) -> None:
        con.execute(
            f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )

    return _write_with_receipt(wh, rc, write)


def _mark_revoked(wh: Path, asset_id: str, at: datetime, rc: _Receipt) -> str | None:
    """Claim the revoke's receipt, then mark the row. ``_write_with_receipt`` outcomes."""

    def write(con: duckdb.DuckDBPyConnection) -> None:
        # The headline and the approved rows carry dimension labels; a revoked pin
        # is never served, so it keeps its hashes and ids and drops its labels.
        con.execute(
            f"UPDATE {TABLE} SET revoked_at = ?, revoke_ledger_entry_id = ?, "
            "active_key = NULL, headline = '', approved_rows_json = '[]' "
            "WHERE asset_id = ? AND revoked_at IS NULL",
            [at, rc.entry_id, asset_id],
        )

    return _write_with_receipt(wh, rc, write)


def _record_replay(wh: Path, rc: _Receipt) -> str | None:
    """Claim a replay's receipt. A replay writes no register row."""
    return _write_with_receipt(wh, rc, None)


def _reread_pin(wh: Path, asset_id: str) -> dict[str, Any] | None | str:
    """The pin as the register holds it now; ``"unreadable"`` if it cannot be read."""
    try:
        return _load_pin(wh, asset_id)
    except Exception:  # noqa: BLE001 - not knowing is reported as not knowing
        return "unreadable"


def _stamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        at = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return _z(at)
    return ip._iso_z(value)


def _public(pin: Mapping[str, Any], withheld: str | None = None) -> dict[str, Any]:
    return {
        "asset_id": pin["asset_id"],
        "space_id": pin["space_id"] or None,
        "insight_id": pin["insight_id"],
        "measure": pin["measure"],
        "dimension": pin["dimension"],
        "kind": pin["kind"],
        "headline": None if withheld else pin["headline"],
        "headline_withheld": withheld,
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
    wh = _warehouse(executor)
    rows = _read(wh, "space_id = ? AND revoked_at IS NULL", [_space_key(space_id)])
    if not rows:
        return []
    loaded = _verified(wh)
    return [_public(r, _headline_withheld(loaded, r, wh)) for r in rows]


def _verified(wh: Path) -> Any:
    """The verified ontology in use now, or None (unverifiable fails closed)."""
    from dms_executor.generative_ask import cached_verify_violations, load_verified_ontology

    try:
        loaded = load_verified_ontology(wh)
    except Exception:  # noqa: BLE001 - an ontology that cannot load cannot clear a label
        return None
    if loaded is None or cached_verify_violations(loaded) != []:
        return None
    return loaded


def _dimension_of(pin: Mapping[str, Any]) -> tuple[str, str, dict[str, str]] | None:
    try:
        d = json.loads(pin["definition_json"])
    except (TypeError, ValueError):
        return None
    obj, attr, via = d.get("dim_object"), d.get("dim_attribute"), d.get("via")
    if not (isinstance(obj, str) and obj and isinstance(attr, str) and attr):
        return None
    if not isinstance(via, list) or not all(
        isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v) for v in via
    ):
        return None
    return obj, attr, {k: v for k, v in via}


def _pii_hit(loaded: Any, obj: str, attr: str, wh: Path) -> bool:
    """INS-PROOF-01's PII gate on the pinned dimension, now. Unknown object: a hit.

    The gate reads views through the serving connection (a view over a marked
    table carries the mark); a warehouse that cannot be opened is a hit.
    """
    from dms_executor.demo_warehouse import connect_serving

    if loaded is None or obj not in loaded.objects:
        return True
    try:
        con = connect_serving(wh)
    except Exception:  # noqa: BLE001 - a catalog that cannot be read clears nothing
        return True
    try:
        return attr.casefold() in ip._pii_columns(loaded, obj, con)
    finally:
        con.close()


def _headline_withheld(loaded: Any, pin: Mapping[str, Any], wh: Path) -> str | None:
    """Why a stored headline (it names labels) may not be listed now, or None."""
    if loaded is None:
        return "ontology_unverified"
    dim = _dimension_of(pin)
    if dim is None:
        return "definition_changed"
    return "pii_dimension" if _pii_hit(loaded, dim[0], dim[1], wh) else None


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
# what a pin depends on
# --------------------------------------------------------------------------


def _dependencies(loaded: Any, measure: str, sql: str) -> list[dict[str, str]]:
    """Ontology items the pinned SQL was compiled from, with the version approved.

    The measure; every object whose relation's tables the SQL reads; every link
    between two of those objects. A replay refuses when any of them is no longer
    served at that version (revoked, re-versioned or gone).
    """
    read = ip._read_tables(sql) or frozenset()
    objects = {
        name for name, o in loaded.objects.items()
        if (t := ip._read_tables(str(o.relation or ""))) and t <= read
    }
    links = {
        name for name, link in loaded.links.items()
        if link.from_object in objects and link.to_object in objects
    }
    out: list[dict[str, str]] = []
    for meta in ip._item_meta(loaded):
        payload = meta.get("payload")
        kind = str(meta.get("kind") or "")
        name = str(payload.get("name") or "") if isinstance(payload, dict) else ""
        if (kind == "measure" and name == measure) or (
            kind == "object" and name in objects) or (kind == "link" and name in links):
            out.append({"kind": kind, "name": name, "item_id": str(meta.get("id") or ""),
                        "version_id": str(meta.get("version_id") or "")})
    return sorted(out, key=lambda d: (d["kind"], d["name"], d["item_id"]))


def _dependencies_hold(loaded: Any, deps: Any) -> bool:
    """Every recorded item still served, same id, same version. Nothing recorded: no."""
    if not isinstance(deps, list) or not deps:
        return False
    served = {
        str(meta.get("id") or ""): str(meta.get("version_id") or "")
        for meta in ip._item_meta(loaded)
    }
    ids = set(loaded.__dict__.get("_served_ids", ()) or ())
    for dep in deps:
        if not isinstance(dep, dict):
            return False
        item_id = str(dep.get("item_id") or "")
        if not item_id or item_id not in ids or served.get(item_id) != dep.get("version_id"):
            return False
    return True


def _infra_code(run: ip.InsightRun) -> str | None:
    """The engine failure behind an empty re-prove, if that is what it was."""
    for entry in run.refused:
        base = str(entry.get("code") or "").split(":", 1)[0]
        if base in INFRA_CODES:
            return base
    return None


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
        return _fail(_infra_code(run) or "insight_not_found")
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

    raw_token = str(token or "")
    tok = _spend(raw_token)
    if isinstance(tok, str):
        return _fail(tok)
    if tok.source != "approved_store":
        return _fail("measure_not_approved")
    run = _reprove(executor, tok.space_id, tok.insight_id)
    if not run.proven:
        infra = _infra_code(run)
        if infra is not None:
            # Cortex, not the data: say so, and let the steward retry this token.
            _give_back(raw_token, tok)
            return _fail(infra)
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
    loaded = _verified(wh)
    deps = _dependencies(loaded, p.measure, p.sql) if loaded is not None else []
    if not p.dim_object or not p.dim_attribute or not deps:
        # Replay could not re-check what this pin rests on: never pin it.
        return _fail("measure_not_approved")
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
        rc = _Receipt(entry_id, seq, PIN_EVENT, asset_id)
        try:
            outcome = _insert_pin(wh, {
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
                    **{k: d[k] for k in ("measure", "grain", "expression", "item_id",
                                         "version_id", "source")},
                    "dim_object": p.dim_object,
                    "dim_attribute": p.dim_attribute,
                    "via": [list(v) for v in p.via],
                    "deps": deps,
                }),
                "watermarks_json": json.dumps(p.proof["watermarks"]),
                "approved_at": approved_at,
                "approved_by": actor,
                "ledger_entry_id": entry_id,
                "active_key": _active_key(space, tok.sql_hash),
            }, rc)
        except Exception:  # noqa: BLE001 - an outcome nobody reported: re-read
            outcome = "unknown"
        if outcome == "repeated":
            # This receipt already backs a pin, a replay or a revoke: it is not
            # evidence of this append. Nothing was written.
            return _fail(REPEATED, ledger_entry_id=entry_id)
        if outcome == "conflict":
            # Another process pinned it between the check and the INSERT.
            return _fail("already_pinned", asset_id=_active_pin(wh, space, tok.sql_hash),
                         orphan_ledger_entry=entry_id)
        if outcome == "unknown":
            # The write raised, but it may have committed. Report what the register
            # holds, never a guess: a row with this entry is the pin.
            now = _reread_pin(wh, asset_id)
            if now == "unreadable":
                return _fail("pin_commit_unknown", ledger_entry_id=entry_id)
            if not (isinstance(now, dict) and now["ledger_entry_id"] == entry_id
                    and now["revoked_at"] is None):
                return _fail("pin_commit_failed", orphan_ledger_entry=entry_id)
            approved_at = now["approved_at"] or approved_at
    env = dict(p.envelope)
    env["audit_id"] = entry_id
    return {
        "ok": True,
        "asset_id": asset_id,
        "insight_id": p.insight_id,
        "approved_at": _stamp(approved_at),
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
    if code == "definition_changed:pii_dimension":
        return TEXTS["pii_dimension"]
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
    # INS-PROOF-01 verify round 4: the tolerance is a fixed function of n_groups,
    # so a replay proof cannot widen its own slack.
    if float(proof["tolerance"]) != ip.conservation_tolerance(int(proof["n_groups"])):
        raise ValueError("tolerance not bound to n_groups")
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
    # Everything else the pinned SQL rests on, before any submit: the items on its
    # path at their approved versions, the PII gate on its dimension, and a
    # recompile that must reproduce the pinned SQL byte for byte.
    if not _dependencies_hold(loaded, definition.get("deps")):
        return _refused(pin, "definition_changed:dependency", "definition_changed")
    dim = _dimension_of(pin)
    if dim is None or dim[0] not in loaded.objects:
        return _refused(pin, "definition_changed:dimension", "definition_changed")
    dim_obj, dim_attr, via = dim
    if _pii_hit(loaded, dim_obj, dim_attr, wh):
        return _refused(pin, "definition_changed:pii_dimension", "definition_changed")

    sql = str(pin["sql_text"])
    if item_content_hash(sql) != pin["sql_hash"]:
        return _refused(pin, "envelope_invalid:sql_hash")
    from dms_executor.ontology import CompiledQuery

    try:
        again = loaded.compile(measure, group_by=[(dim_obj, dim_attr)], via=via or None)
    except Exception:  # noqa: BLE001 - a definition that no longer compiles has changed
        again = None
    if not isinstance(again, CompiledQuery) or item_content_hash(
        ip._wrapper_sql(again.sql, f"{dim_obj}_{dim_attr}", measure)
    ) != pin["sql_hash"]:
        return _refused(pin, "definition_changed:compiled_sql", "definition_changed")
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
    # INS-PROOF-01 round 5: the bare-name lookup above grants Secret.Orders on a
    # grant of Sales.Orders. Every statement replay submits passes main's
    # statement check (serve_gap) first, as the prove path does.
    for stmt in (raw_sql, parts_sql, sql):
        if stmt is not None and ip._statement_gap(stmt, set(access.granted)) is not None:
            return _refused(pin, "ungranted")
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
    tol = ip.conservation_tolerance(groups)
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
    executor: Any,
    asset_id: str,
    *,
    ledger_append: Callable[..., Any],
    actor: str | None = None,
) -> dict[str, Any]:
    """One replay of an active pin: a verdict, its envelope and proof, one ledger pointer.

    ``actor`` is the deployment actor (DR-0004 Option A) the receipt names.
    """
    if not ip.insight_proof_enabled():
        return _off()
    from dms_executor.envelope import assert_envelope_valid

    wh = _warehouse(executor)
    pin = _load_pin(wh, asset_id)
    if pin is None or pin["revoked_at"] is not None:
        return _fail("pin_not_found")
    done = _execute(executor, pin)
    infra = str(done.code or "").split(":", 1)[0]
    if infra in INFRA_CODES:
        # Cortex, not the data: a 503 as at preview and confirm, and no
        # ``insight.replayed`` entry records an engine outage as a verdict.
        return _fail(infra)
    # A revoke may have landed while the SQL ran. Re-read under the lock revoke
    # holds for its own append, so a revoked pin is never served or receipted.
    with _PIN_LOCK:
        now = _load_pin(wh, asset_id)
        if now is None or now["revoked_at"] is not None:
            return _fail("pin_not_found")
        got = _append(
            ledger_append,
            REPLAY_EVENT,
            {
                "asset_id": pin["asset_id"],
                "sql_hash": pin["sql_hash"],
                "result_hash": done.result_hash,
                "verdict": done.verdict,
            },
            actor,
        )
        if isinstance(got, str):
            return _fail(got)
        entry_id, seq = got
        rc = _Receipt(entry_id, seq, REPLAY_EVENT, str(pin["asset_id"]))
        try:
            outcome = _record_replay(wh, rc)
        except Exception:  # noqa: BLE001 - an outcome nobody reported: re-read
            outcome = "unknown"
        if outcome == "unknown":
            try:
                outcome = None if _receipt_recorded(wh, rc) else "unrecorded"
            except Exception:  # noqa: BLE001 - unreadable is not recorded
                outcome = "unrecorded"
    if outcome == "repeated":
        # The pin's own entry, an earlier replay's, or a revoke's: not this append.
        return _fail(REPEATED, ledger_entry_id=entry_id)
    if outcome is not None:
        # Unrecorded, a later reuse of this receipt could not be caught: not served.
        return _fail("ledger_receipt_unrecorded", orphan_ledger_entry=entry_id)
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


def demote_replay(
    res: Mapping[str, Any], code: str, *, orphan_ledger_entry: str | None = None
) -> dict[str, Any]:
    """A served replay that may not be served (its audit row did not land), as ABSTAIN.

    INS-PROOF-02 rule (g) for replays: the envelope is a fresh ``build_abstain``
    with no rows, values or SQL, and the receipt that was appended is named as
    ``orphan_ledger_entry``. Never mutates ``res``.
    """
    from dms_executor.envelope import assert_envelope_valid

    old = res.get("envelope") or {}
    insight_id = str(res.get("insight_id") or "")
    env = abstain.build_abstain(
        reason=code,
        stage=ip.STAGE,
        question="insight:" + insight_id,
        answer_id="ans_insproof_replay_" + hashlib.sha256(
            f"{res.get('asset_id')}|{code}".encode()
        ).hexdigest()[:16],
        text=_refusal_text(code, ""),
        values=[],
        rows=[],
        sql_used=None,
        space_id=old.get("space_id"),
        session_id=old.get("session_id"),
    )
    assert_envelope_valid(env)
    proof = dict(res.get("proof") or {})
    for key in ("result_hash", "reexec_result_hash", "drift"):
        if key in proof:
            proof[key] = None
    proof.update(verdict="refused", code=code)
    out = {**res, "verdict": "refused", "code": code, "envelope": env, "proof": proof,
           "trace": ExecutedTrace()}
    if orphan_ledger_entry:
        out["orphan_ledger_entry"] = orphan_ledger_entry
    return out


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
        entry_id, seq = got
        at = _clock()
        rc = _Receipt(entry_id, seq, REVOKE_EVENT, str(pin["asset_id"]))
        try:
            outcome = _mark_revoked(wh, str(pin["asset_id"]), at, rc)
        except Exception:  # noqa: BLE001 - an outcome nobody reported: re-read
            outcome = "unknown"
        if outcome == "repeated":
            return _fail(REPEATED, ledger_entry_id=entry_id)
        if outcome is not None:
            # The write raised (or hit a rule), but it may have committed. Report
            # what the register holds: revoked with this entry is a revoke.
            now = _reread_pin(wh, str(pin["asset_id"]))
            if now == "unreadable":
                return _fail("revoke_commit_unknown", revoke_ledger_entry_id=entry_id)
            if not (isinstance(now, dict) and now["revoke_ledger_entry_id"] == entry_id
                    and now["revoked_at"] is not None):
                return _fail("revoke_commit_failed", orphan_ledger_entry=entry_id)
            at = now["revoked_at"]
    return {"ok": True, "asset_id": pin["asset_id"], "revoked_at": _stamp(at),
            "revoke_ledger_entry_id": entry_id}
