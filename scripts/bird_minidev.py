"""A1-02 BIRD Mini-Dev harness -- grade envelope rows against gold SQL.

Never commit the 500-question corpus. Fetch or load it at run time.
No accuracy target. First live run is a Platform baseline, not PASS.

Numeric match (not 4 dp absolute):
  Integers compare exactly (so 29-digit ids never go through Decimal.quantize).
  Non-integers match when |a-b| <= max(ABS_TOL, REL_TOL * max(|a|,|b|))
  with REL_TOL=1e-6 (large magnitudes) and ABS_TOL=1e-9 (near-zero).
  1e-8 vs 2e-8 disagree; 1_000_000.00014 vs 1_000_000.00015 agree.

Until Cortex ROUTER-1: live Cortex scoring requires CORTEX_FREEROUTE_LEARN=0,
a fresh CORTEX_ROUTE_STORE, and recorded store snapshots. Missing those is
CONFIG, not a score. Offline/--self-check skips the freeze.

Served provider/model are copied from the Cortex/ask JSON when present;
otherwise ``unknown``. Never guessed from FreeRoute plans or env.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
_EXECUTOR = ROOT / "packages" / "executor"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(_EXECUTOR) not in sys.path:
    sys.path.insert(0, str(_EXECUTOR))

from score_bound import bound_line, bound_pct, zero_wrong_summary_bounded  # noqa: E402

SYNTHETIC = ROOT / "tests" / "fixtures" / "bird_minidev" / "synthetic.json"
MINIDEV_N = 500
MINIDEV_DBS = 11
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2
EXIT_BLOCKED = 3
UNKNOWN = "unknown"
REL_TOL = Decimal("1e-6")
ABS_TOL = Decimal("1e-9")
LEARN_ENV = "CORTEX_FREEROUTE_LEARN"
STORE_ENV = "CORTEX_ROUTE_STORE"
CONFIDENT = frozenset(
    {"L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS"}
)
# Reported keys only. Never infer from badge, route, question, or FreeRoute plan.
_PROVIDER_PATHS: tuple[tuple[str, ...], ...] = (
    ("provider",),
    ("served_provider",),
    ("llm_provider",),
    ("model_provider",),
    ("provenance", "provider"),
    ("telemetry", "provider"),
    ("served", "provider"),
)
_MODEL_PATHS: tuple[tuple[str, ...], ...] = (
    ("model",),
    ("served_model",),
    ("llm_model",),
    ("model_id",),
    ("model_name",),
    ("provenance", "model"),
    ("telemetry", "model"),
    ("served", "model"),
)

SYNTHETIC_SETUP: tuple[str, ...] = (
    "CREATE TABLE widgets (id INTEGER, name VARCHAR, price DOUBLE)",
    "INSERT INTO widgets VALUES (1, 'alpha', 1.0), (2, 'beta', 2.5)",
)

AskFn = Callable[[str], dict[str, Any]]
GoldFn = Callable[[str], tuple[list[dict[str, Any]] | None, str | None]]


def git_sha(root: Path = ROOT) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(root), text=True, timeout=5
        )
        return out.strip()
    except Exception:  # noqa: BLE001 - metadata must not fail the scorer
        return os.environ.get("GIT_SHA") or os.environ.get("GITHUB_SHA") or "unknown"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def norm_cell(value: Any) -> tuple[str, Any]:
    """Hashable comparable cell. Never quantize (29+ digit strings must not crash)."""
    if value is None:
        return ("z", None)
    if isinstance(value, str) and not value.strip():
        return ("z", None)
    num = _as_decimal(value)
    if num is not None:
        return ("n", num)
    return ("s", str(value).strip().casefold())


def cells_equal(left: Any, right: Any) -> bool:
    a_kind, a_val = norm_cell(left)
    b_kind, b_val = norm_cell(right)
    if a_kind != b_kind:
        return False
    if a_kind == "z":
        return True
    if a_kind == "s":
        return a_val == b_val
    da, db = a_val, b_val
    with localcontext() as ctx:
        ctx.prec = max(50, abs(da.adjusted()) + 8, abs(db.adjusted()) + 8)
        if da == db:
            return True
        if da == da.to_integral_value() and db == db.to_integral_value():
            return da == db
        diff = abs(da - db)
        mag = max(abs(da), abs(db))
        return diff <= max(ABS_TOL, REL_TOL * mag)


def _row_cells(row: Any) -> list[Any]:
    if isinstance(row, dict):
        return list(row.values())
    if isinstance(row, (list, tuple)):
        return list(row)
    return [row]


def row_equal(left: Any, right: Any) -> bool:
    """Column-order-insensitive cell multiset match."""
    a = _row_cells(left)
    b = _row_cells(right)
    if len(a) != len(b):
        return False
    used = [False] * len(b)
    for cell in a:
        found = False
        for i, other in enumerate(b):
            if used[i]:
                continue
            if cells_equal(cell, other):
                used[i] = True
                found = True
                break
        if not found:
            return False
    return True


def rows_equal(gold: Sequence[Any], got: Sequence[Any]) -> bool:
    """Multiset of rows. ponytail: O(n*m) match; Mini-Dev rows are small."""
    if len(gold) != len(got):
        return False
    used = [False] * len(got)
    for row in gold:
        found = False
        for i, other in enumerate(got):
            if used[i]:
                continue
            if row_equal(row, other):
                used[i] = True
                found = True
                break
        if not found:
            return False
    return True


def is_confident(env: Mapping[str, Any]) -> bool:
    badge = str(env.get("badge") or "")
    if env.get("abstained") or badge == "ABSTAIN":
        return False
    return badge in CONFIDENT


def envelope_rows(env: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = env.get("rows") or []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def grade_envelope(env: Mapping[str, Any], gold_rows: Sequence[Any]) -> str:
    """OK / LAYER / ABSTAIN / WRONG. Confident empty is WRONG even if gold is empty."""
    if not is_confident(env):
        return "ABSTAIN"
    got = envelope_rows(env)
    if not got:
        return "WRONG"
    if not rows_equal(gold_rows, got):
        return "WRONG"
    badge = str(env.get("badge") or "")
    if badge.startswith("L0"):
        return "OK"
    return "LAYER"


def gold_error_dominating(gold_err: str | None, verdict: str) -> str:
    """GOLD_ERROR wins when gold SQL failed; otherwise the envelope verdict stands.

    Both branches are required: a constant GOLD_ERROR or a pass-through fails tests.
    """
    if gold_err:
        return "GOLD_ERROR"
    return verdict


def pg_gold_error(exc: BaseException) -> str:
    """dead_connection vs sql_error. Connection failures must not look like bad SQL."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & {
        "OperationalError",
        "InterfaceError",
        "SourceConnectionError",
    }:
        return "dead_connection"
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return "dead_connection"
    return "sql_error"


def run_pg_gold(
    sql: str, connect: Callable[[], Any]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    try:
        con = connect()
    except Exception as exc:  # noqa: BLE001
        return None, pg_gold_error(exc)
    try:
        try:
            con.execute("SET default_transaction_read_only = on")
        except Exception:  # noqa: BLE001 - read-only is best-effort
            pass
        cur = con.cursor()
        try:
            cur.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
            return rows, None
        finally:
            cur.close()
    except Exception as exc:  # noqa: BLE001
        return None, pg_gold_error(exc)
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass


def _nested(obj: Any, path: Sequence[str]) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _reported_str(env: Mapping[str, Any], paths: Sequence[tuple[str, ...]]) -> str:
    for path in paths:
        val = _nested(env, path)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return UNKNOWN


def served_from_response(env: Mapping[str, Any] | None) -> dict[str, str]:
    """Copy provider/model as Cortex/ask reported them. Never guess."""
    if not isinstance(env, dict):
        return {"provider": UNKNOWN, "model": UNKNOWN}
    return {
        "provider": _reported_str(env, _PROVIDER_PATHS),
        "model": _reported_str(env, _MODEL_PATHS),
    }


def served_mix(cases: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in cases:
        provider = str(row.get("provider") or UNKNOWN)
        model = str(row.get("model") or UNKNOWN)
        counts[f"{provider}/{model}"] += 1
    return dict(sorted(counts.items()))


def setup_payload(
    *,
    learn: str | None,
    store_id: str | None,
    fresh: bool | None,
    hash_before: str | None,
    mix: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "learn": learn,
        "store_id": store_id,
        "fresh": fresh,
        "hash_before": hash_before,
        "served_mix": dict(mix),
    }


def setup_fingerprint(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _count_store_rows(path: Path, data: bytes) -> int:
    if data.startswith(b"SQLite format 3"):
        import sqlite3

        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            names = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            total = 0
            for name in names:
                total += int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            return total
        finally:
            con.close()
    text = data.decode("utf-8", "replace").strip()
    if not text:
        return 0
    if text[0] in "[{":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return len(parsed)
        if isinstance(parsed, dict):
            for key in ("routes", "rows", "items"):
                val = parsed.get(key)
                if isinstance(val, list):
                    return len(val)
            return len(parsed)
    return sum(1 for line in text.splitlines() if line.strip())


def snapshot_route_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "fresh": True,
            "row_count": 0,
            "hash": hashlib.sha256(b"").hexdigest(),
            "error": None,
        }
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {
            "exists": True,
            "fresh": False,
            "row_count": None,
            "hash": None,
            "error": str(exc),
        }
    digest = hashlib.sha256(data).hexdigest()
    if not data:
        return {
            "exists": True,
            "fresh": True,
            "row_count": 0,
            "hash": digest,
            "error": None,
        }
    try:
        rows = _count_store_rows(path, data)
    except Exception as exc:  # noqa: BLE001
        return {
            "exists": True,
            "fresh": False,
            "row_count": None,
            "hash": digest,
            "error": str(exc),
        }
    return {
        "exists": True,
        "fresh": rows == 0,
        "row_count": rows,
        "hash": digest,
        "error": None,
    }


def require_freeroute_frozen(env: Mapping[str, str]) -> dict[str, Any] | str:
    """Metadata dict, or an error string. Error means do not produce a score."""
    learn = (env.get(LEARN_ENV) or "").strip()
    if learn != "0":
        return (
            "CONFIG: Mini-Dev live scoring refuses while Cortex FreeRoute "
            "learning is not off. Set CORTEX_FREEROUTE_LEARN=0 and a fresh "
            f"{STORE_ENV}. Until Cortex ROUTER-1, a scored run must not train "
            "the router. Unset or 1 is not a score."
        )
    store = (env.get(STORE_ENV) or "").strip()
    if not store:
        return (
            f"CONFIG: {STORE_ENV} is required so the run can record store state."
        )
    snap = snapshot_route_store(Path(store))
    if snap["error"]:
        return f"CONFIG: cannot record route-store state: {snap['error']}"
    if not snap["fresh"]:
        return (
            f"CONFIG: {STORE_ENV} is not fresh "
            f"(rows={snap['row_count']} hash={snap['hash']}). "
            "Point the run at an empty store."
        )
    return {
        "learn": "0",
        "store": store,
        "store_id": store,
        "fresh": True,
        "row_count_before": snap["row_count"],
        "hash_before": snap["hash"],
        "exists_before": snap["exists"],
    }


def finish_freeroute(meta: Mapping[str, Any]) -> dict[str, Any] | str:
    store = Path(str(meta["store"]))
    snap = snapshot_route_store(store)
    if snap["error"] or snap["hash"] is None:
        return (
            "CONFIG: could not record route-store after-hash. No scored result. "
            f"{snap.get('error') or 'missing hash'}"
        )
    out = dict(meta)
    out["row_count_after"] = snap["row_count"]
    out["hash_after"] = snap["hash"]
    return out


def load_minidev_source(
    source: str, *, dest_dir: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load Mini-Dev JSON from a path or URL. Writes fetches under .tmp only."""
    src = source.strip()
    fetched = False
    if src.startswith("http://") or src.startswith("https://"):
        import httpx

        resp = httpx.get(src, timeout=60.0, follow_redirects=True)
        resp.raise_for_status()
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / "mini_dev_fetched.json"
        path.write_bytes(resp.content)
        fetched = True
        data = resp.content
    else:
        path = Path(src)
        data = path.read_bytes()
    parsed = json.loads(data.decode("utf-8"))
    if isinstance(parsed, dict):
        questions = parsed.get("questions") or parsed.get("data") or []
    else:
        questions = parsed
    if not isinstance(questions, list):
        raise ValueError("Mini-Dev JSON must be a list of questions")
    out: list[dict[str, Any]] = []
    for row in questions:
        if isinstance(row, dict):
            out.append(row)
    meta = {
        "source": src,
        "fetched": fetched,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "n": len(out),
        "path": str(path),
    }
    return out, meta


def validate_full_minidev(
    questions: Sequence[Mapping[str, Any]], *, limit: int | None
) -> str | None:
    if limit is not None:
        return None
    n = len(questions)
    if n != MINIDEV_N:
        return (
            f"CONFIG: Mini-Dev is {MINIDEV_N} questions over {MINIDEV_DBS} databases; "
            f"this file has {n}. Refusing to score a shrunk set. "
            "Pass --limit for smoke (printed, not a Mini-Dev score)."
        )
    dbs = {str(q.get("db_id") or "") for q in questions}
    dbs.discard("")
    if len(dbs) != MINIDEV_DBS:
        return (
            f"CONFIG: Mini-Dev is {MINIDEV_DBS} databases; this file has {len(dbs)}. "
            "Refusing to score a shrunk set."
        )
    return None


def gold_sql_of(question: Mapping[str, Any]) -> str:
    return str(
        question.get("SQL")
        or question.get("sql")
        or question.get("gold_sql")
        or ""
    )


def asked_text(question: Mapping[str, Any], *, with_evidence: bool) -> str:
    text = str(question.get("question") or "")
    if with_evidence:
        evidence = str(question.get("evidence") or "").strip()
        if evidence:
            return f"{text}\nEvidence: {evidence}"
    return text


def score_cases(
    questions: Sequence[Mapping[str, Any]],
    *,
    ask_fn: AskFn,
    gold_fn: GoldFn,
    with_evidence: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in questions:
        sql = gold_sql_of(item)
        gold_rows, gold_err = gold_fn(sql)
        env = ask_fn(asked_text(item, with_evidence=with_evidence))
        served = served_from_response(env)
        if gold_err:
            verdict = gold_error_dominating(gold_err, "OK")
        else:
            verdict = gold_error_dominating(None, grade_envelope(env, gold_rows or []))
        got = envelope_rows(env)
        rows.append(
            {
                "id": item.get("question_id", item.get("id")),
                "db_id": item.get("db_id"),
                "difficulty": item.get("difficulty") or "unknown",
                "verdict": verdict,
                "badge": env.get("badge"),
                "gold_rows": len(gold_rows or []),
                "got_rows": len(got),
                "gold_error": gold_err,
                "provider": served["provider"],
                "model": served["model"],
            }
        )
    return rows


def _slice_tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tallies = {"OK": 0, "LAYER": 0, "ABSTAIN": 0, "WRONG": 0, "GOLD_ERROR": 0}
    for row in rows:
        key = str(row.get("verdict") or "")
        if key in tallies:
            tallies[key] += 1
    n = tallies["OK"] + tallies["LAYER"] + tallies["ABSTAIN"] + tallies["WRONG"]
    answered = tallies["OK"] + tallies["LAYER"]
    wrong = tallies["WRONG"]
    denom = answered + wrong
    return {
        "n": n,
        "ok": tallies["OK"],
        "layer": tallies["LAYER"],
        "abstain": tallies["ABSTAIN"],
        "wrong": wrong,
        "gold_error": tallies["GOLD_ERROR"],
        "answered": answered,
        "right": tallies["OK"] + tallies["LAYER"],
        "ex_on_answered_pct": round(100.0 * answered / denom, 2) if denom else None,
        "abstain_rate_pct": round(100.0 * tallies["ABSTAIN"] / n, 2) if n else None,
        "bound_pct": bound_pct(answered),
    }


def breakdown(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        label = str(row.get(key) or "unknown")
        groups.setdefault(label, []).append(row)
    return {label: _slice_tally(items) for label, items in sorted(groups.items())}


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = _slice_tally(rows)
    summary["by_db_id"] = breakdown(rows, "db_id")
    summary["by_difficulty"] = breakdown(rows, "difficulty")
    summary["served_mix"] = served_mix(rows)
    return summary


def print_summary(summary: Mapping[str, Any], *, limit: int | None, total: int) -> None:
    n = int(summary["n"])
    answered = int(summary["answered"])
    ex = summary["ex_on_answered_pct"]
    ex_s = "n/a" if ex is None else f"{ex:.2f} pct"
    abs_s = summary["abstain_rate_pct"]
    abs_txt = "n/a" if abs_s is None else f"{abs_s:.2f} pct"
    print(
        f"n={n} answered={answered} RIGHT={summary['right']} "
        f"ABSTAIN={summary['abstain']} WRONG={summary['wrong']} "
        f"GOLD_ERROR={summary['gold_error']} (excluded from n)"
    )
    print(f"EX on answered={ex_s} abstain rate={abs_txt}")
    print(f"  {bound_line(answered)}")
    if limit is not None:
        print(f"limit={limit} of {total} (smoke, not a Mini-Dev score)")
    print("per db_id:")
    for db_id, row in (summary.get("by_db_id") or {}).items():
        print(
            f"  {db_id} n={row['n']} RIGHT={row['right']} ABSTAIN={row['abstain']} "
            f"WRONG={row['wrong']} GOLD_ERROR={row['gold_error']}"
        )
    print("per difficulty:")
    for diff, row in (summary.get("by_difficulty") or {}).items():
        print(
            f"  {diff} n={row['n']} RIGHT={row['right']} ABSTAIN={row['abstain']} "
            f"WRONG={row['wrong']} GOLD_ERROR={row['gold_error']}"
        )
    print("served provider/model counts:")
    mix = summary.get("served_mix") or {}
    if not mix:
        print("  (none)")
    for key, count in mix.items():
        print(f"  {key} {count}")


def artifact_path(env: Mapping[str, str] | None = None) -> Path:
    art = Path((env or os.environ).get("DMS_SCORE_DIR") or (ROOT / ".tmp"))
    art.mkdir(parents=True, exist_ok=True)
    return art / "score_bird_minidev.json"


def write_minidev_artifact(report: Mapping[str, Any], env: Mapping[str, str] | None = None) -> Path:
    path = artifact_path(env)
    path.write_text(json.dumps(dict(report), indent=2, default=str) + "\n", encoding="utf-8")
    return path


def compare_runs(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    force: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Refuse to treat different setup fingerprints as the same measurement."""
    fa = left.get("setup_fingerprint")
    fb = right.get("setup_fingerprint")
    same = bool(fa) and bool(fb) and fa == fb
    if not same and not force:
        return EXIT_CONFIG, {
            "kind": "dms.score_bird_minidev.compare",
            "refused": True,
            "reason": "setup_fingerprint_mismatch",
            "left": fa,
            "right": fb,
            "note": (
                "Refuse to compare Mini-Dev runs with different setup fingerprints. "
                "Learning flag, store state, and served-model mix must match. "
                "ROUTER-1 vs pre-ROUTER-1 is not the same setup. "
                "Pass --force-cross-setup to label a cross-setup comparison."
            ),
        }
    label = "same-setup" if same else "cross-setup"
    ls = left.get("summary") or {}
    rs = right.get("summary") or {}
    result = {
        "kind": "dms.score_bird_minidev.compare",
        "refused": False,
        "comparison": label,
        "left_fingerprint": fa,
        "right_fingerprint": fb,
        "left_n": ls.get("n"),
        "right_n": rs.get("n"),
        "left_wrong": ls.get("wrong"),
        "right_wrong": rs.get("wrong"),
        "left_answered": ls.get("answered"),
        "right_answered": rs.get("answered"),
    }
    if not same:
        result["note"] = (
            "cross-setup comparison (--force-cross-setup). "
            "Not the same setup. Do not quote as a ROUTER-1 delta."
        )
    return EXIT_PASS, result


_GOLD_MOD: Any = None


def _minidev_gold_mod() -> Any:
    """Executor gold helper without importing dms_executor.__init__ (CortexClient)."""
    global _GOLD_MOD
    if _GOLD_MOD is None:
        import importlib.util

        path = _EXECUTOR / "dms_executor" / "minidev_gold.py"
        spec = importlib.util.spec_from_file_location("_dms_minidev_gold", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GOLD_MOD = mod
    return _GOLD_MOD


def duck_gold_fn(setup: Sequence[str] = SYNTHETIC_SETUP) -> GoldFn:
    def _run(sql: str) -> tuple[list[dict[str, Any]] | None, str | None]:
        try:
            return _minidev_gold_mod().duck_gold_rows(sql, setup=setup), None
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "OperationalError":
                return None, pg_gold_error(exc)
            return None, "sql_error"

    return _run


def pg_gold_fn(connect: Callable[[], Any]) -> GoldFn:
    def _run(sql: str) -> tuple[list[dict[str, Any]] | None, str | None]:
        return run_pg_gold(sql, connect)

    return _run


def pg_connect_from_env(env: Mapping[str, str]) -> Callable[[], Any]:
    dsn = (env.get("BIRD_PG_DSN") or "").strip()
    host = (env.get("BIRD_PG_HOST") or "").strip()
    if not dsn and not host:
        raise ValueError(
            "BIRD_PG_DSN (or BIRD_PG_HOST) is required for gold SQL on --minidev --live."
        )

    def _connect() -> Any:
        import psycopg

        if dsn:
            return psycopg.connect(dsn)
        return psycopg.connect(
            host=host,
            port=int(env.get("BIRD_PG_PORT") or 5432),
            user=env.get("BIRD_PG_USER") or "postgres",
            password=env.get("BIRD_PG_PASSWORD") or "",
            dbname=env.get("BIRD_PG_DB") or "bird_minidev",
            connect_timeout=int(env.get("BIRD_PG_CONNECT_TIMEOUT") or 10),
        )

    return _connect


def _confident(badge: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"badge": badge, "abstained": False, "rows": rows, "text": "ok"}


def minidev_self_check() -> list[str]:
    """Offline grader plants. Not a live Mini-Dev score. No Cortex, no freeze."""
    errs: list[str] = []
    questions, meta = load_minidev_source(str(SYNTHETIC), dest_dir=ROOT / ".tmp")
    if len(questions) < 4:
        errs.append("synthetic Mini-Dev fixture too small")
    gold = duck_gold_fn()

    count_gold, count_err = gold(gold_sql_of(questions[0]))
    if count_err or not count_gold:
        errs.append("synthetic count gold must execute")
        return errs

    ok_env = _confident("L0_CERTIFIED", [{"n": 2}])
    if grade_envelope(ok_env, count_gold) != "OK":
        errs.append("matching confident rows must be OK")

    swapped = _confident("L0_CERTIFIED", [{"n": 2}])
    if grade_envelope(swapped, [{"x": 2}]) != "OK":
        errs.append("column order must not flip a match")

    names_gold, _ = gold(gold_sql_of(questions[1]))
    order_env = _confident(
        "L0_CERTIFIED",
        [{"other": "beta"}, {"other": "alpha"}],
    )
    if names_gold and grade_envelope(order_env, names_gold) != "OK":
        errs.append("row/column order must not flip a name list")

    avg_gold, _ = gold(gold_sql_of(questions[2]))
    noisy = _confident("L2_VALIDATED", [{"avg_price": 1.7500001}])
    if avg_gold and grade_envelope(noisy, avg_gold) != "LAYER":
        errs.append("float noise must not flip AVG; L2 match is LAYER")

    wrong_env = _confident("L0_CERTIFIED", [{"n": 99}])
    if grade_envelope(wrong_env, count_gold) != "WRONG":
        errs.append("confident wrong row set must be WRONG")

    empty_env = _confident("L2_VALIDATED", [])
    if grade_envelope(empty_env, count_gold) != "WRONG":
        errs.append("confident empty must be WRONG")

    abs_env = {"badge": "ABSTAIN", "abstained": True, "rows": [], "text": "no"}
    if grade_envelope(abs_env, count_gold) != "ABSTAIN":
        errs.append("abstention must be ABSTAIN")

    broken_rows, broken_err = gold(gold_sql_of(questions[3]))
    if not broken_err or gold_error_dominating(broken_err, "OK") != "GOLD_ERROR":
        errs.append("failing gold SQL must be GOLD_ERROR")
    if broken_rows:
        errs.append("failing gold SQL must not return rows")

    if gold_error_dominating(None, "WRONG") != "WRONG":
        errs.append("gold_error_dominating must keep WRONG when gold ran")
    if gold_error_dominating("sql_error", "OK") != "GOLD_ERROR":
        errs.append("gold_error_dominating must prefer GOLD_ERROR")

    huge = "1" * 40
    try:
        if not cells_equal(huge, huge):
            errs.append("norm_cell must equal a 40-digit numeric string to itself")
    except Exception as exc:  # noqa: BLE001
        errs.append(f"norm_cell crashed on 40-digit numeric string: {exc}")
    if cells_equal("1e-8", "2e-8"):
        errs.append("1e-8 vs 2e-8 must not match (4 dp absolute is too coarse)")
    if not cells_equal("1000000.00014", "1000000.00015"):
        errs.append("large-magnitude relative tolerance must match")

    class OperationalError(Exception):
        pass

    if pg_gold_error(OperationalError("connection refused")) != "dead_connection":
        errs.append("pg_gold_error dead_connection branch missing")

    def _dead() -> Any:
        raise OperationalError("connection refused")

    rows, kind = run_pg_gold("SELECT 1", _dead)
    if rows is not None or kind != "dead_connection":
        errs.append("run_pg_gold must pin dead_connection on connect failure")

    served_u = served_from_response({"badge": "L0_CERTIFIED", "rows": [{"n": 1}]})
    if served_u != {"provider": UNKNOWN, "model": UNKNOWN}:
        errs.append("missing Cortex model/provider must record unknown, never guess")
    served_r = served_from_response({"provider": "groq", "model": "llama-3.3"})
    if served_r != {"provider": "groq", "model": "llama-3.3"}:
        errs.append("reported provider/model must be copied")

    left = {
        "setup_fingerprint": "aaa",
        "summary": {"n": 10, "wrong": 0, "answered": 4},
    }
    right = {
        "setup_fingerprint": "bbb",
        "summary": {"n": 10, "wrong": 1, "answered": 5},
    }
    code, cmp_ = compare_runs(left, right, force=False)
    if code != EXIT_CONFIG or not cmp_.get("refused"):
        errs.append("compare must refuse different fingerprints")
    code_f, cmp_f = compare_runs(left, right, force=True)
    if code_f != EXIT_PASS or cmp_f.get("comparison") != "cross-setup":
        errs.append("forced compare must label cross-setup")

    if not zero_wrong_summary_bounded(
        ["MEASURED: WRONG=0. Not PASS. Not COMPLETE.", bound_line(3)]
    ):
        errs.append("Mini-Dev WRONG=0 line must carry n and bound")

    _ = meta
    return errs


def run_minidev(
    questions: Sequence[Mapping[str, Any]],
    *,
    ask_fn: AskFn,
    gold_fn: GoldFn,
    data_meta: Mapping[str, Any],
    with_evidence: bool = False,
    limit: int | None = None,
    cortex: bool = False,
    env: Mapping[str, str] | None = None,
    started: str | None = None,
    write: bool = True,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Score Mini-Dev. On freeze/store failure returns CONFIG and no report."""
    env = env or os.environ
    freeze: dict[str, Any] | None = None
    if cortex:
        frozen = require_freeroute_frozen(env)
        if isinstance(frozen, str):
            return EXIT_CONFIG, None, frozen
        freeze = frozen

    sliced = list(questions)
    total = len(sliced)
    if limit is not None:
        sliced = sliced[: max(limit, 0)]
    cases = score_cases(
        sliced, ask_fn=ask_fn, gold_fn=gold_fn, with_evidence=with_evidence
    )
    summary = summarize(cases)
    mix = summary["served_mix"]
    if cortex:
        finished = finish_freeroute(freeze or {})
        if isinstance(finished, str):
            return EXIT_CONFIG, None, finished
        freeze = finished
        payload = setup_payload(
            learn=freeze.get("learn"),
            store_id=freeze.get("store_id"),
            fresh=freeze.get("fresh"),
            hash_before=freeze.get("hash_before"),
            mix=mix,
        )
        freeroute_out = dict(freeze)
    else:
        payload = setup_payload(
            learn=None,
            store_id=None,
            fresh=None,
            hash_before=None,
            mix=mix,
        )
        freeroute_out = {
            "cortex": False,
            "learn": None,
            "note": "offline/no-Cortex; freeze not required",
        }

    ended = utc_now()
    report = {
        "kind": "dms.score_bird_minidev",
        "complete": False,
        "passed": False,
        "with_evidence": bool(with_evidence),
        "limit": limit,
        "sha": git_sha(),
        "started": started or ended,
        "ended": ended,
        "data": dict(data_meta),
        "data_bytes": data_meta.get("bytes"),
        "freeroute": freeroute_out,
        "setup": payload,
        "setup_fingerprint": setup_fingerprint(payload),
        "served_mix": mix,
        "summary": summary,
        "cases": cases,
        "note": (
            "MEASURED baseline harness. Not PASS. Not COMPLETE. "
            "Not a quoted Mini-Dev score. Live baseline is Platform after merge."
        ),
    }
    if write:
        write_minidev_artifact(report, env)
    return EXIT_PASS, report, None


def run_minidev_cli(args: Any, env: Mapping[str, str]) -> int:
    if getattr(args, "compare", None):
        left_p, right_p = args.compare
        try:
            left = json.loads(Path(left_p).read_text(encoding="utf-8"))
            right = json.loads(Path(right_p).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"CONFIG: cannot read compare artifacts: {exc}")
            print("VERDICT: CONFIG")
            return EXIT_CONFIG
        code, result = compare_runs(
            left, right, force=bool(getattr(args, "force_cross_setup", False))
        )
        print(json.dumps(result, indent=2))
        if result.get("refused"):
            print("VERDICT: CONFIG (cross-setup compare refused)")
        elif result.get("comparison") == "cross-setup":
            print("VERDICT: MEASURED cross-setup (forced). Not the same setup.")
        else:
            print("VERDICT: MEASURED same-setup compare. Not PASS.")
        return code

    source = str(args.minidev)
    try:
        questions, data_meta = load_minidev_source(
            source, dest_dir=ROOT / ".tmp" / "bird_minidev"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"CONFIG: cannot load Mini-Dev JSON: {exc}")
        print("VERDICT: CONFIG")
        return EXIT_CONFIG

    limit = args.limit
    gate = validate_full_minidev(questions, limit=limit)
    if gate:
        print(gate)
        print("VERDICT: CONFIG")
        return EXIT_CONFIG

    cortex = bool(args.live) and not bool(args.offline)
    if not args.live and not args.offline:
        print(
            "CONFIG: --minidev needs --live (Cortex + Postgres gold) "
            "or --offline (grader only; --self-check covers synthetic)."
        )
        print("VERDICT: CONFIG")
        return EXIT_CONFIG

    if cortex:
        try:
            from score_bird import _ask_live, live_url

            url = live_url(dict(env), args.url)
        except ValueError as exc:
            print(f"CONFIG: {exc}")
            print("VERDICT: CONFIG")
            return EXIT_CONFIG
        space = (args.space or env.get("BIRD_SPACE_ID") or "").strip()
        if not space:
            from score_bird import BIRD_SPACE

            space = BIRD_SPACE

        def ask_fn(question: str) -> dict[str, Any]:
            return _ask_live(url, question, space, args.timeout)

        try:
            gold_fn = pg_gold_fn(pg_connect_from_env(env))
        except ValueError as exc:
            print(f"CONFIG: {exc}")
            print("VERDICT: CONFIG")
            return EXIT_CONFIG
    else:
        gold_fn = duck_gold_fn()

        def ask_fn(question: str) -> dict[str, Any]:
            return {
                "badge": "ABSTAIN",
                "abstained": True,
                "rows": [],
                "text": f"offline no Cortex: {question[:40]}",
            }

    started = utc_now()
    code, report, err = run_minidev(
        questions,
        ask_fn=ask_fn,
        gold_fn=gold_fn,
        data_meta=data_meta,
        with_evidence=bool(args.with_evidence),
        limit=limit,
        cortex=cortex,
        env=env,
        started=started,
        write=True,
    )
    if err:
        print(err)
        print("VERDICT: CONFIG")
        return code
    assert report is not None
    print_summary(report["summary"], limit=limit, total=int(data_meta.get("n") or 0))
    print(f"sha={report['sha']} data_bytes={report['data_bytes']}")
    print(f"setup_fingerprint={report['setup_fingerprint']}")
    wrong = int(report["summary"]["wrong"])
    if wrong:
        print("MEASURED: WRONG>0. Not PASS. First run is baseline.")
        return EXIT_FAIL
    print("MEASURED: WRONG=0. Not PASS. Not COMPLETE. Not a quoted Mini-Dev score.")
    print(f"  {bound_line(int(report['summary']['answered']))}")
    return EXIT_PASS
