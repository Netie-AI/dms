"""BANK-02 (dms#269): the audit scrub is on the request path and must stay linear.

``scrub_counted`` runs on every ``POST /v1/chat/ask`` question and on every executed
statement, and Python's ``re`` holds the GIL while it matches, so a pattern that
backtracks does not slow one ask, it stalls the whole API process. The verify of
b426b53 measured ``password="`` followed by 38 backslashes at 27 s (x2.6 per two
more characters), a 54-character question that never returned, and quadratic time
on repeated ``a://b:`` and ``/* `` prefixes.

This fuzzes EVERY compiled pattern in ``ask_audit_scrub`` (found by introspection, so a
pattern added later is covered without editing this file) with adversarial input built
from that pattern's own alphabet: a long run of each metacharacter and quote, unterminated
quotes, repeated keywords and prefixes, nested braces, long base64 blobs and a seeded
random mix. Each input is run at a ladder of sizes up to the production window sizes
(12,000 for a question, 52,000 for SQL) and at 1 MB. The assertions are conservative on
purpose, for a CI runner: an absolute ceiling per input, a growth check (four times the
input must not take more than about eight times as long), and a ceiling at every
size from 12 to 40 characters that fails on exponential time before the ladder reaches a
size that would hang the suite.

Each check fails on b426b53 (its ``_ASSIGN`` quoted branch is exponential on a backslash
run, and its URL, curl and comment patterns are quadratic).
"""

from __future__ import annotations

import functools
import json
import os
import random
import re
import subprocess
import sys
import time
from collections.abc import Callable

import dms_core.control_plane.ask_audit_scrub as scrub_mod
import pytest
from dms_core.control_plane.ask_audit_scrub import has_sql_statement, scrub_counted

PATTERNS = {name: p for name, p in vars(scrub_mod).items() if isinstance(p, re.Pattern)}

#: Production windows: the recorded question cap plus margin, and the SQL cap plus margin.
QUESTION_WINDOW = 12_000
SQL_WINDOW = 52_000
MEGABYTE = 1_000_000

#: Seconds. Generous for a slow shared runner, tiny next to a backtracking stall.
CEILING = 0.25
MEGABYTE_CEILING = 4.0
#: A linear regex takes about four times as long on four times the input; allow eight, plus
#: a floor so a one-millisecond measurement is not judged on noise.
GROWTH = 8.0
FLOOR = 0.03
#: Inputs of 12 to 40 characters take microseconds in a linear pattern; b426b53's
#: backtracking regex took 0.08 s at 36 characters of ``password="`` and backslashes,
#: x2.7 per two more.
SMALL_SIZES = (12, 16, 20, 24, 28, 32, 36, 40)
SMALL_CEILING = 0.05

_FIXED_KEYWORDS = [
    "password",
    "token",
    "secret",
    "authorization",
    "bearer",
    "cookie",
    "curl",
    "login",
    "mysql",
    "sshpass",
    "redis-cli",
    "sig",
    "identified by",
    "with password",
    "-----BEGIN PRIVATE KEY-----",
    "a://b:",
    "/* ",
    "-- ",
    "x://u:DMSMASK_email_01 ",
    "h:5432:",
]
_SEPARATORS = ["", " ", "=", ":", '"', "_"]
_QUOTE_AND_BACKSLASH = ['"', "\\"]


def _repeat(unit: str, n: int) -> str:
    return (unit * (n // max(1, len(unit)) + 1))[:n]


def _alphabet(pattern: re.Pattern[str]) -> list[str]:
    """Punctuation the pattern itself uses, plus the quote, escape and space characters."""
    own = {c for c in pattern.pattern if c.isprintable() and not c.isalnum() and c != " "}
    return sorted(own | set("\"'\\ :=@%/*-_.\n{}[]()"))


def _families(pattern: re.Pattern[str]) -> dict[str, Callable[[int], str]]:
    fams: dict[str, Callable[[int], str]] = {}
    for c in _alphabet(pattern):
        fams[f"run of {c!r}"] = lambda n, c=c: c * n
    fams["nested braces"] = lambda n: "{" * (n // 2) + "}" * (n - n // 2)
    keywords = sorted(
        {w for w in re.findall(r"[A-Za-z]{5,}", pattern.pattern)} | set(_FIXED_KEYWORDS)
    )
    for kw in keywords:
        for sep in _SEPARATORS:
            fams[f"{kw!r}+{sep!r} repeated"] = lambda n, kw=kw, sep=sep: _repeat(kw + sep, n)
        for q in _QUOTE_AND_BACKSLASH:
            fams[f"{kw!r}=unterminated {q!r} run"] = lambda n, kw=kw, q=q: (
                kw + "=" + '"' + q * max(0, n - len(kw) - 2)
            )
    alphabet = "".join(_alphabet(pattern)) + "abAB19password"
    fams["seeded random mix"] = lambda n: "".join(random.Random(11).choices(alphabet, k=n))
    base64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    fams["base64 blob"] = lambda n: "".join(random.Random(12).choices(base64, k=n))
    fams["base64 lines"] = lambda n: "\n".join(
        "".join(random.Random(i).choices(base64, k=64)) for i in range(max(1, n // 65))
    )
    return fams


def _best(fn: Callable[[str], object], text: str) -> float:
    """One run; a second only when the first was slow enough to be worth doubting."""
    t0 = time.perf_counter()
    fn(text)
    first = time.perf_counter() - t0
    if first < 0.05:
        return first
    t0 = time.perf_counter()
    fn(text)
    return min(first, time.perf_counter() - t0)


def _scan(pattern: re.Pattern[str]) -> Callable[[str], object]:
    return lambda text: sum(1 for _ in pattern.finditer(text))


def test_every_pattern_in_the_module_is_covered() -> None:
    assert len(PATTERNS) >= 20, sorted(PATTERNS)
    # the ones that bit: the quoted-value assignment, the URL, curl, cookie and comment readers
    assert {"_ASSIGN_TAIL", "_CORE_RX", "_CURL_USER", "_KEY_BODY", "_BLOB", "_SQL_LITERAL"} <= set(
        PATTERNS
    )


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_no_pattern_is_super_linear_on_adversarial_input(name: str) -> None:
    scan = _scan(PATTERNS[name])
    ladder = [*SMALL_SIZES, 400, 1_600, 6_400, QUESTION_WINDOW, SQL_WINDOW]
    for family, make in _families(PATTERNS[name]).items():
        t: dict[int, float] = {}
        for n in ladder:
            t[n] = _best(scan, make(n))
            if n in SMALL_SIZES:
                # Exponential time shows here, in steps small enough that the first
                # failing size still finishes: the larger sizes are not reached.
                assert t[n] < SMALL_CEILING, f"{name} on {family} at {n} chars took {t[n]:.3f}s"
            if n in (QUESTION_WINDOW, SQL_WINDOW):
                assert t[n] < CEILING, f"{name} on {family} at {n} chars took {t[n]:.3f}s"
        for small, big in ((400, 1_600), (1_600, 6_400), (QUESTION_WINDOW, SQL_WINDOW)):
            assert t[big] < GROWTH * t[small] + FLOOR, (
                f"{name} on {family}: {small} chars {t[small]:.4f}s, {big} chars {t[big]:.4f}s"
            )


MIX_ALPHABET = "password=:" + chr(34) + chr(39) + chr(92) + " /*-@%" + chr(10)


def _megabyte_inputs() -> dict[str, str]:
    base64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    return {
        "backslash run": "\\" * MEGABYTE,
        "double-quote run": '"' * MEGABYTE,
        "single-quote run": "'" * MEGABYTE,
        "a://b: repeated": _repeat("a://b:", MEGABYTE),
        "block comment opens": _repeat("/* ", MEGABYTE),
        "password_ repeated": _repeat("password_", MEGABYTE),
        'password=" then backslashes': 'password="' + "\\" * MEGABYTE,
        "base64 blob": "".join(random.Random(13).choices(base64, k=MEGABYTE)),
        "seeded random mix": "".join(random.Random(14).choices(MIX_ALPHABET, k=MEGABYTE)),
    }


_MEGABYTE = _megabyte_inputs()


# A pattern that stalls on 1 MB would hang this process, and a hung test run is not a
# failure anyone can read. So the 1 MB scans run in a child process under a deadline; a
# stall is reported as the pattern that was being read when the deadline passed.
MEGABYTE_DEADLINE = 240.0
ENTRY_DEADLINE = 90.0


def _run_child(call: str, deadline: float) -> tuple[list[str], str | None]:
    """Run ``call`` in a child that has this module loaded; (its output lines, a stall note)."""
    code = (
        "import importlib.util as u;"
        f"s = u.spec_from_file_location('redos_child', {__file__!r});"
        "m = u.module_from_spec(s); s.loader.exec_module(m);" + call
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    try:
        done = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=deadline,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        text = raw.decode() if isinstance(raw, bytes) else raw
        return text.splitlines(), f"did not finish in {deadline:.0f}s"
    if done.returncode != 0:
        raise AssertionError(done.stderr.decode(errors="replace")[-800:])
    return done.stdout.decode().splitlines(), None


def _child_megabyte() -> None:
    """Child: scan every pattern over every 1 MB input; one JSON line per pattern."""
    for name in sorted(PATTERNS):
        scan = _scan(PATTERNS[name])
        times = {}
        for family, text in _MEGABYTE.items():
            t0 = time.perf_counter()
            scan(text)
            times[family] = time.perf_counter() - t0
        print(json.dumps({"pattern": name, "times": times}), flush=True)


@functools.cache
def _megabyte_results() -> tuple[dict[str, dict[str, float]], str | None]:
    lines, stall = _run_child("m._child_megabyte()", MEGABYTE_DEADLINE)
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    return {r["pattern"]: r["times"] for r in rows}, stall


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_no_pattern_stalls_on_a_megabyte(name: str) -> None:
    results, stall = _megabyte_results()
    assert name in results, (
        f"{name} was not measured on 1 MB: the run {stall or 'ended before it'}; "
        f"measured: {sorted(results)}"
    )
    for family, took in results[name].items():
        assert took < MEGABYTE_CEILING, f"{name} on {family} took {took:.2f}s for 1 MB"


# --- the entry points, with the exact repros from the verify ------------------------------


def test_the_exact_backslash_repro_returns_at_once() -> None:
    for n in (30, 44, 54, 38, 10_000):
        text = 'password="' + "\\" * n
        t0 = time.perf_counter()
        clean, _ = scrub_counted(text)
        took = time.perf_counter() - t0
        assert took < CEILING, f'{n} backslashes after password=" took {took:.2f}s'
        assert isinstance(clean, str)


def test_the_quadratic_repros_stay_flat_at_the_production_windows() -> None:
    cases = {
        "url_repeat": _repeat("a://b:", SQL_WINDOW),
        "curl_repeat": _repeat("curl ", SQL_WINDOW),
        "comment_open_repeat": _repeat("/* ", SQL_WINDOW),
        "password_us_repeat": _repeat("password_", SQL_WINDOW),
    }
    for family, text in cases.items():
        t0 = time.perf_counter()
        scrub_counted(text, sql=True)
        took = time.perf_counter() - t0
        assert took < 1.0, f"{family} as {SQL_WINDOW} chars of SQL took {took:.2f}s"
        q = text[:QUESTION_WINDOW]
        t0 = time.perf_counter()
        scrub_counted(q)
        took = time.perf_counter() - t0
        assert took < 0.5, f"{family} as {QUESTION_WINDOW} chars of question took {took:.2f}s"


def _child_entry_points() -> None:
    """Child: the entry points on input far past any window; one JSON object."""
    huge = "/* " * (MEGABYTE // 3)
    out: dict[str, object] = {}
    t0 = time.perf_counter()
    clean, _ = scrub_counted(huge, sql=True)
    out["scrub_counted_seconds"] = time.perf_counter() - t0
    out["scrub_window_marker"] = "[scrub window:" in clean
    t0 = time.perf_counter()
    out["comments_found"] = has_sql_statement(huge)
    out["has_sql_statement_seconds"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    out["many_found"] = has_sql_statement("/**/" * 200_000)
    out["many_seconds"] = time.perf_counter() - t0
    print(json.dumps(out), flush=True)


@functools.cache
def _entry_point_results() -> tuple[dict[str, object], str | None]:
    lines, stall = _run_child("m._child_entry_points()", ENTRY_DEADLINE)
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    return (rows[0] if rows else {}), stall


def test_the_whole_scrub_stays_bounded_whatever_it_is_given() -> None:
    res, stall = _entry_point_results()
    assert res, f"1 MB handed straight to scrub_counted: the run {stall or 'printed nothing'}"
    assert float(res["scrub_counted_seconds"]) < 2.0, (
        f"1 MB handed straight to scrub_counted took {res['scrub_counted_seconds']}s"
    )
    assert res["scrub_window_marker"], "what was not read is dropped and said so, not returned raw"


def test_has_sql_statement_is_linear_and_still_right() -> None:
    res, stall = _entry_point_results()
    assert res, f"has_sql_statement on 1 MB: the run {stall or 'printed nothing'}"
    assert float(res["has_sql_statement_seconds"]) < CEILING, (
        f"has_sql_statement on 1 MB of '/* ' took {res['has_sql_statement_seconds']}s"
    )
    assert res["comments_found"] is True, "an unterminated block comment is text, as it always was"
    assert res["many_found"] is True  # all comment, but longer than the scan: assume text
    assert float(res["many_seconds"]) < CEILING

    assert has_sql_statement("   -- just a comment\n  /* and another */  ") is False
    assert has_sql_statement("-- document retrieval (no SQL)") is False
    assert has_sql_statement("/* c */ SELECT 1") is True
    assert has_sql_statement("") is False and has_sql_statement(None) is False


# --- the whole scrub, not only its regexes ------------------------------------------------
#
# A regex can be linear and the loop around it quadratic (b426b53's credential-name pass
# re-read an unquoted value to its end at every `password=` in `password=password=...`).
# These run scrub_counted itself, in both modes, at the production windows.

_BS = chr(92)
_ENTRY_UNITS = [
    "password=",
    "password:",
    'password:"',
    "password_",
    "api_key=",
    "token is ",
    "secret.",
    "Authorization: Basic ",
    "Cookie: ",
    "login a password ",
    "h:5432:d:u:",
    "mysql -p",
    "sig=",
    "a://b:",
    "curl ",
    "/* ",
    "-- ",
    "'",
    '"',
    _BS,
    '{"password":"',
    "identified by ",
    "with password ",
    "-----BEGIN PRIVATE KEY-----",
    "x://u:DMSMASK_email_01 ",
    "[redacted]",
    "pass",
]
ENTRY_CEILING = 0.6


def _entry_families() -> dict[str, Callable[[int], str]]:
    fams: dict[str, Callable[[int], str]] = {}
    for unit in _ENTRY_UNITS:
        fams[f"{unit!r} repeated"] = lambda n, unit=unit: _repeat(unit, n)
    fams["unterminated quote then backslashes"] = lambda n: 'password="' + _BS * n
    fams["unterminated quote then credential words"] = lambda n: (
        "password='" + _repeat("password=", n)
    )
    fams["seeded random mix"] = lambda n: "".join(random.Random(21).choices(MIX_ALPHABET, k=n))
    fams["base64 blob"] = lambda n: "".join(
        random.Random(22).choices(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", k=n
        )
    )
    return fams


@pytest.mark.parametrize("sql", [False, True], ids=["question", "sql"])
def test_scrub_counted_is_linear_on_every_family(sql: bool) -> None:
    window = SQL_WINDOW if sql else QUESTION_WINDOW

    def run(text: str) -> object:
        return scrub_counted(text, sql=sql)

    for family, make in _entry_families().items():
        small, big = _best(run, make(window // 4)), _best(run, make(window))
        assert big < ENTRY_CEILING, f"{family}: {window} chars took {big:.3f}s (sql={sql})"
        assert big < GROWTH * small + FLOOR, (
            f"{family}: {window // 4} chars {small:.4f}s, {window} chars {big:.4f}s (sql={sql})"
        )
