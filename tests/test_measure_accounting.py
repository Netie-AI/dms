"""Exact accounting on the confirmed lane (dms#284, step 10): the question and the plan agree.

The confirmed lane states a real figure under a confirmed basis, so it must never answer a
question whose condition the plan dropped ("...in the US" answered by the unfiltered total) nor
a plan that filters on a literal the question never stated. Three earlier gates were word lists
and each was one word, script or plural away from a gap; the gate is now exact accounting with
default deny (``measure_use.account_for_question``). This file proves it three ways:

* the verifier's exact repros, through ``POST /v1/chat/ask`` (abstained, the named gap, no
  ``measure_basis``, no rows, nothing executed), with the positive controls still answering;
* a metamorphic battery (a suffix the plan does not bind is refused wherever it is inserted) and a
  seeded hostile-alphabet property test against an independent oracle;
* mutations: loosen each rule in turn and a named check goes red.

Non-ASCII samples are built from code points (``_u``) so no invisible character lives in source.
"""

from __future__ import annotations

import random
import re
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from dms_core.control_plane.onto_store import OntologyStore
from dms_executor import measure_use as mu
from dms_executor.measure_use import FitVerdict, measure_fit
from dms_executor.ontology import Measure
from dms_executor.space_ontology import set_ontology_store
from test_connect_ask_02 import (
    BUDGET,
    COUNT,
    DISTRICTS,
    SCHOOLS,
    _assert_answer,
    _assert_basis,
    _oracle,
    _reasons,
    _Rigged,
)
from test_ingest_fixes_277 import _Rig
from test_measure_use import BUDGET as BUDGET_MEASURE
from test_measure_use import SCOUNT as COUNT_MEASURE
from test_onto_confirm_01 import _Env, _Ledger
from test_onto_derive_01 import _assert_abstain, make_minter


@pytest.fixture()
def minter(monkeypatch: pytest.MonkeyPatch) -> Any:
    return make_minter(monkeypatch)


@pytest.fixture()
def store() -> Iterator[OntologyStore]:
    fresh = OntologyStore()
    prev = set_ontology_store(fresh)
    try:
        yield fresh
    finally:
        set_ontology_store(prev)


@pytest.fixture()
def rigged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minter: Any, store: OntologyStore
) -> _Rigged:
    """The Space of test_connect_ask_02: typed schools data, the proposer's measures proposed."""
    rig = _Rig(tmp_path, monkeypatch, minter)
    space = rig.space("Measure accounting")
    rig.ingest(space)
    env = _Env(rig=rig, space=space, sid=UUID(space), store=store, ledger=_Ledger())
    return _Rigged(env)


def _u(*codepoints: int) -> str:
    return "".join(chr(c) for c in codepoints)


CJK = _u(0x5317, 0x533A)
TAMIL = _u(0x0B95, 0x0BC7, 0x0BB2, 0x0BBE)
EMOJI = _u(0x1F334)
N_TILDE_ORTH = _u(0xF1) + "orth"
FULLWIDTH_TWO = _u(0xFF12)
FULLWIDTH_US = _u(0xFF35, 0xFF33)
ZERO_WIDTH = _u(0x200B)
ARABIC = _u(0x645, 0x62F, 0x631, 0x633, 0x629)
GREEK = _u(0x395, 0x3BB, 0x3BB, 0x3AC, 0x3B4, 0x3B1)

NORTH = (DISTRICTS, "name", "=", "North")
BY_ID_2 = (SCHOOLS, "district_id", "=", 2)
POOL = {BUDGET_MEASURE.name: BUDGET_MEASURE}
COUNT_POOL = {COUNT_MEASURE.name: COUNT_MEASURE}
TOTAL = "360.75"


def _fit(question: str, *filters: tuple[Any, ...], pool: dict[str, Measure] = POOL) -> FitVerdict:
    return measure_fit(question, pool, filters=list(filters))


# ===================================================================== the verifier's repros
#
# (question, the plan's filters). Each is a question the confirmed lane must NOT answer with
# this plan: either the plan drops what the question states, or it filters on a literal the
# question never stated. Questions are about schools; the measure is schools_budget_sum.

Q = "What is the total budget of schools"
REPROS: list[tuple[str, str, tuple[Any, ...]]] = [
    # id, question, filters
    ("us-capitals", f"{Q} in the US?", ()),
    ("us-lowercase", f"{Q} in the us?", ()),
    ("or", f"{Q} in OR?", ()),
    ("it", f"{Q} in IT?", ()),
    ("in", f"{Q} in IN?", ()),
    ("a", f"{Q} in A?", ()),
    ("wills", "What is the total budget of Wills schools?", ()),
    ("doe", "What is the total budget of Doe schools?", ()),
    ("cans", "What is the total budget of Cans schools?", ()),
    ("gets", "What is the total budget of Gets schools?", ()),
    ("lists", "What is the total budget of Lists schools?", ()),
    ("show-us", "Show us the total budget of schools", ()),
    ("where-region", f"{Q} where region is north?", ()),
    ("cjk", f"{Q} in {CJK}?", ()),
    ("tamil", f"{Q} in {TAMIL}?", ()),
    ("emoji", f"{Q} in {EMOJI}?", ()),
    ("fullwidth-digit", f"{Q} in district {FULLWIDTH_TWO}?", ()),
    ("fullwidth-capitals", f"{Q} in {FULLWIDTH_US}?", ()),
    ("backtick", f"{Q} in `north`?", ()),
    ("dollar", f"{Q} over $5?", ()),
    ("ampersand", f"{Q} & districts?", ()),
    ("star", f"{Q} * 2?", ()),
    ("minus", f"{Q} - 2?", ()),
    ("apostrophe", "What's the total budget of schools?", ()),
    # the mirror: the plan filters on a literal the question did not state, or states otherwise
    ("sign", f"{Q} in district -2?", (BY_ID_2,)),
    ("sign-reverse", f"{Q} in district 2?", ((SCHOOLS, "district_id", "=", -2),)),
    ("decimal-tail", f"{Q} in district 1.5?", ((SCHOOLS, "district_id", "=", 5),)),
    ("decimal-swap", f"{Q} in district 5.1?", ((SCHOOLS, "district_id", "=", 1.5),)),
    ("date-swap", f"{Q} opened 2024-05-01?", ((SCHOOLS, "opened", "=", "2024-01-05"),)),
    ("added-value", f"{Q}?", ((DISTRICTS, "name", "=", "South"),)),
    (
        "added-in-value",
        f"{Q} in the North district?",
        ((DISTRICTS, "name", "IN", ["North", "South"]),),
    ),
    ("wrong-case", f"{Q} in the north district?", ((DISTRICTS, "name", "=", "North"),)),
    ("prefix", f"{Q} in the Northern district?", ((DISTRICTS, "name", "=", "North"),)),
    ("operator", f"{Q} in district 2?", ((SCHOOLS, "district_id", ">", 2),)),
    ("bool", f"{Q} in district 1?", ((SCHOOLS, "district_id", "=", True),)),
    ("null", f"{Q} in district 1?", ((SCHOOLS, "district_id", "=", None),)),
]
REPRO_IDS = [r[0] for r in REPROS]


def _repro_gate_refuses() -> None:
    """The pure check every repro must pass; the mutation tests re-run it under a loosened rule."""
    for rid, question, filters in REPROS:
        verdict = _fit(question, *filters)
        assert not verdict.ok, f"{rid}: {question!r} was accepted"
        assert verdict.reason in {"unbound_constraint", "unstated_filter"}, (rid, verdict)


@pytest.mark.parametrize(("rid", "question", "filters"), REPROS, ids=REPRO_IDS)
def test_repro_the_gate_refuses_it(rid: str, question: str, filters: tuple[Any, ...]) -> None:
    verdict = _fit(question, *filters)
    assert verdict.ok is False and verdict.pick == BUDGET_MEASURE.name, (rid, verdict)
    assert verdict.reason in {"unbound_constraint", "unstated_filter"}, (rid, verdict)
    assert verdict.detail, "the refusal names what nothing accounts for"


def test_repro_every_row_is_refused_in_one_pass() -> None:
    _repro_gate_refuses()


def _assert_refused_at_the_envelope(rigged: _Rigged, env: dict[str, Any]) -> None:
    text = _assert_abstain(env, "no_declared_measure")
    assert env["abstained"] is True and env["rows"] == [] and env["values"] == []
    assert "measure_basis" not in env
    assert TOTAL not in text and "310.75" not in text, text
    assert rigged.executed == [] and rigged.rig.cortex.asks == []
    assert any("fit_disagrees:" in a for a in env["assumptions"]), env["assumptions"]


@pytest.mark.parametrize(("rid", "question", "filters"), REPROS, ids=REPRO_IDS)
def test_repro_envelope_a_typed_plan_that_drops_or_adds_a_condition_abstains(
    rigged: _Rigged, rid: str, question: str, filters: tuple[Any, ...]
) -> None:
    """POST /v1/chat/ask: the typed plan names the confirmed measure, the question says
    something else. No figure, no basis, no rows, nothing executed, no fallback ask."""
    rigged.confirm(BUDGET)
    env = rigged.plan(question, BUDGET, filters=filters)
    _assert_refused_at_the_envelope(rigged, env)


@pytest.mark.parametrize(
    ("rid", "question"),
    [(rid, q) for rid, q, filters in REPROS if not filters],
    ids=[rid for rid, _q, filters in REPROS if not filters],
)
def test_repro_envelope_a_ranking_with_no_sql_abstains_rather_than_state_the_total(
    rigged: _Rigged, rid: str, question: str
) -> None:
    """Cortex ranked the measure first and sent no SQL: the ranked plan has no filter either."""
    rigged.confirm(BUDGET)
    env = rigged.space.ask(
        question, {"plan_source": "ontology_plan", "ontology": {"metrics": [{"id": BUDGET}]}}
    )
    _assert_refused_at_the_envelope(rigged, env)


# ===================================================================== positive controls


def test_control_the_plain_questions_still_answer_with_oracle_rows_and_text(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    oracle = _oracle(rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools")
    assert oracle == [{"v": Decimal("360.75")}]
    for question in (
        "What is the total budget of schools?",
        "show me the total budget of schools",
        "Show me the total budget of schools",
        "Can you give me the total budget of all the schools, please?",
    ):
        env = rigged.plan(question, BUDGET)
        _assert_answer(env, oracle, TOTAL)
        _assert_basis(env, rigged, BUDGET)


def test_control_the_same_with_the_literal_bound_answers_the_filtered_figure(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    oracle = _oracle(
        rigged.rig.lake,
        "SELECT SUM(s.budget) AS v FROM bronze.public_schools s "
        "JOIN bronze.public_districts d ON s.district_id = d.district_id WHERE d.name = 'North'",
    )
    assert oracle == [{"v": Decimal("310.75")}]
    for question in (
        "What is the total budget of schools in the North district?",
        "show me the total budget of schools in the North district",
    ):
        env = rigged.plan(question, BUDGET, filters=(NORTH,))
        _assert_answer(env, oracle, "310.75")
        assert TOTAL not in str(env["text"])
        _assert_basis(env, rigged, BUDGET)


def test_control_a_numeric_literal_bound_to_the_plan_answers(rigged: _Rigged) -> None:
    rigged.confirm(BUDGET)
    oracle = _oracle(
        rigged.rig.lake, "SELECT SUM(budget) AS v FROM bronze.public_schools WHERE district_id = 2"
    )
    question = "What is the total budget of schools in district 2?"
    env = rigged.plan(question, BUDGET, filters=(BY_ID_2,))
    _assert_answer(env, oracle, "50")


def test_control_a_grouped_question_answers_when_the_plan_groups_by_the_word(
    rigged: _Rigged,
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(
        "What is the total budget of schools by district?",
        BUDGET,
        group_by=((SCHOOLS, "district_id"),),
    )
    oracle = _oracle(
        rigged.rig.lake,
        "SELECT district_id, SUM(budget) AS v FROM bronze.public_schools GROUP BY district_id",
    )
    _assert_answer(env, oracle, "310.75")
    # ... but the same words with an ungrouped plan state one total as a per-district answer
    env = rigged.plan("What is the total budget of schools by district?", BUDGET)
    _assert_abstain(env, "no_declared_measure")


def test_control_a_count_question_answers(rigged: _Rigged) -> None:
    rigged.confirm(COUNT)
    env = rigged.plan("How many schools are there?", COUNT)
    oracle = _oracle(rigged.rig.lake, "SELECT COUNT(*) AS v FROM bronze.public_schools")
    _assert_answer(env, oracle, "4")
    env = rigged.plan("How many schools are there in the US?", COUNT)
    _assert_abstain(env, "no_declared_measure")


# ===================================================================== a threshold or window


def test_a_typed_plan_with_a_threshold_or_a_window_the_question_never_states_abstains(
    rigged: _Rigged,
) -> None:
    """``keep_gt`` drops groups below a threshold and ``offset`` skips rows: conditions the
    plan states and the question did not. Never a confirmed figure, never an execution."""
    rigged.confirm(BUDGET)
    for extra in ({"keep_gt": 100.0}, {"offset": 2}):
        payload = {
            "query_plan": {"measure": BUDGET, "group_by": [[SCHOOLS, "district_id"]], **extra},
            "plan_source": "ontology_plan",
        }
        env = rigged.space.ask("What is the total budget of schools by district?", payload)
        assert env["abstained"] is True and env["rows"] == [], env["text"]
        assert "measure_basis" not in env and rigged.executed == [], extra
    # the threshold alone reaches the fit (the window is refused earlier by its own gate)
    payload = {
        "query_plan": {"measure": BUDGET, "keep_gt": 100.0},
        "plan_source": "ontology_plan",
    }
    env = rigged.space.ask("What is the total budget of schools?", payload)
    _assert_abstain(env, "no_declared_measure")
    assert f"{BUDGET} fit_disagrees:unstated_filter ('keep_gt')" in _reasons(env)


# ===================================================================== metamorphic battery

#: The test's own copy of the closed frame list (not read from the implementation).
FRAME = set(
    "the a an of is are was were what which how much many do does there and in on at for with "
    "from all any give show tell find list please can could would you".split()
)
OLD_FRAME_LIST = (
    "a an the of is are was were be been what which who whom whose how much do does did there "
    "here me my we our us you your i it its this that these those and or to in on at for with "
    "from where has have had give show tell get find list please can could would will all any "
    "every each by per as across"
).split()
US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
    "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
ISO_COUNTRIES = "US GB DE FR MY SG ID IN CN JP KR AU NZ CA BR MX IT ES NL SE NO DK FI PL".split()
NAMES = "Smith John Mary Wills Doe Nguyen Lim Tan Kumar Garcia Gets Lists Cans Wong Li".split()

SUFFIXES: list[str] = [
    *(w.upper() for w in OLD_FRAME_LIST),
    *(w.title() for w in OLD_FRAME_LIST),
    *(w + "s" for w in OLD_FRAME_LIST),
    *(w.title() + "s" for w in OLD_FRAME_LIST),
    *US_STATES,
    *ISO_COUNTRIES,
    *NAMES,
    *(f"in {code}" for code in ("US", "OR", "IT", "IN", "A", "ME", "OK", "HI")),
    *("0", "7", "42", "2024", "007", "1e5", "5th", "q3", "2x"),
    *("-2", "+3", "-0.5", "+0", "--1", "2-3"),
    *("2024-05-01", "01/05/2024", "May 5", "5 May", "2024", "Q3 2024"),
    *("1.5", "0.25", ".5", "5.", "1,000", "1.5.2"),
    *(CJK, TAMIL, N_TILDE_ORTH, ARABIC, GREEK, FULLWIDTH_TWO, FULLWIDTH_US, f"in {CJK}"),
    *(EMOJI, EMOJI * 2, f"in {EMOJI}", _u(0x2764), _u(0x20AC), _u(0xA3) + "5"),
    *('"x"', "'x'", "`x`", "(x)", "[x]", "{x}", "a_b", "x;y", "x:y", "x/y", "x&y", "x|y", "x=y"),
    *("a-b", "$5", "5%", "#1", "@home", "~", "^", "*", "<", ">", "<=", "<>", "!=", "!"),
    *(ZERO_WIDTH, "x" + ZERO_WIDTH + "y", _u(0x2014), _u(0x2018) + "x" + _u(0x2019)),
    *("'; DROP TABLE schools; --", "1=1", "OR 1=1", "x' OR '1'='1", "UNION SELECT 1", "/* x */"),
    *("by schools", "by district", "per school", "each school", "every school", "by", "per"),
    *("top 5", "top five", "greater than 5", "more than 100", "at least 3", "over 100"),
    *("where region is north", "where north", "in the north", "in district 2", "last year"),
    *("excluding schools", "not schools", "only schools", "other than schools"),
]
#: A suffix that is itself a plain lowercase frame word is a legitimate sentence ("i" + "s"), so
#: it is not a condition and is left out; the list in ``FRAME`` is the test's own copy.
#: "Please" before "show me" is a request phrase and "!" last is the closing mark.
SUFFIXES = [s for s in dict.fromkeys(SUFFIXES) if s not in FRAME | {"Please", "!"}]

#: Base questions the gate accepts with the plan beside them (no suffix, no refusal).
BASES: list[tuple[str, str, tuple[Any, ...], dict[str, Measure]]] = [
    ("what", "What is the total budget of schools?", (), POOL),
    ("show-me", "show me the total budget of schools", (), POOL),
    ("north", "What is the total budget of schools in the North district?", (NORTH,), POOL),
    ("how-many", "How many schools are there?", (), COUNT_POOL),
]


def _insert(question: str, suffix: str, where: str) -> str:
    """Put ``suffix`` at the start, in the middle (after the third word) or at the end (before
    the closing mark) of ``question``."""
    body = question.rstrip("?")
    mark = question[len(body) :]
    if where == "start":
        return f"{suffix} {question}"
    if where == "middle":
        words = body.split(" ")
        cut = min(3, len(words) - 1)
        return " ".join([*words[:cut], suffix, *words[cut:]]) + mark
    return f"{body} {suffix}{mark}"


@pytest.mark.parametrize(("bid", "question", "filters", "pool"), BASES, ids=[b[0] for b in BASES])
def test_battery_the_bases_are_accepted_without_a_suffix(
    bid: str, question: str, filters: tuple[Any, ...], pool: dict[str, Measure]
) -> None:
    got = _fit(question, *filters, pool=pool)
    assert got.ok, (bid, got)


def _battery_failures() -> list[str]:
    failures: list[str] = []
    for bid, question, filters, pool in BASES:
        for suffix in SUFFIXES:
            reasons = set()
            for where in ("start", "middle", "end"):
                text = _insert(question, suffix, where)
                got = _fit(text, *filters, pool=pool)
                if got.ok:
                    failures.append(f"{bid}/{where}: {text!r} was accepted")
                reasons.add(got.reason)
            if len(reasons) > 1:
                failures.append(f"{bid}: {suffix!r} is refused for different reasons {reasons}")
    return failures


def test_battery_a_suffix_the_plan_does_not_bind_is_refused_wherever_it_sits() -> None:
    failures = _battery_failures()
    assert not failures, "\n".join(failures[:20])


@pytest.mark.parametrize("suffix", SUFFIXES[::17])
def test_battery_the_refusal_names_the_suffix(suffix: str) -> None:
    """At the end of a question whose own words are all accounted for, the refusal's detail
    carries something of the suffix (a token or a character class), never nothing."""
    got = _fit(_insert(BASES[0][1], suffix, "end"))
    assert not got.ok and got.detail, suffix


def _envelope_sample() -> list[str]:
    return SUFFIXES[::9]


@pytest.mark.parametrize("suffix", _envelope_sample())
def test_battery_envelope_the_typed_plan_abstains_for_a_sample_of_the_suffixes(
    rigged: _Rigged, suffix: str
) -> None:
    rigged.confirm(BUDGET)
    env = rigged.plan(_insert(BASES[0][1], suffix, "end"), BUDGET)
    assert env["abstained"] is True and env["rows"] == [] and "measure_basis" not in env
    assert TOTAL not in str(env["text"]) and rigged.executed == []


def test_battery_a_suffix_the_plan_binds_is_accepted_wherever_it_sits() -> None:
    """The control that makes the battery meaningful: bind the literal and the very same
    sentence is answered, in all three positions."""
    flt = (SCHOOLS, "district_id", "=", 7)
    for where in ("middle", "end"):
        text = _insert(BASES[0][1], "in district 7", where)
        assert _fit(text, flt).ok, text
    unbound = _insert(BASES[0][1], "in district 7", "end")
    assert not _fit(unbound).ok


# ===================================================================== seeded hostile alphabet
#
# An independent oracle: it shares no code with the gate. It strips the plan's literals with a
# regex, splits what is left with itertools.groupby, and decides token by token.

CUES = {
    "total", "sum", "overall", "altogether", "combined", "average", "avg", "mean", "minimum",
    "lowest", "smallest", "least", "maximum", "highest", "largest", "greatest", "biggest",
    "count", "tally", "distinct", "unique",
}  # fmt: skip
LEADING = {("show", "me"), ("tell", "me"), ("give", "me"), ("can", "you"), ("could", "you")}
VOCAB = {"schools", "budget", "sum", "public"}
GROUP_VOCAB: set[str] = set()


def _oracle_word(ch: str) -> bool:
    return unicodedata.category(ch)[0] in ("L", "N", "M")


def _oracle_forms(v: str) -> set[str]:
    out = {v, v + "s"}
    if v.endswith("y"):
        out.add(v[:-1] + "ies")
    if v.endswith("s") and not v.endswith("ss") and len(v) > 3:
        out.add(v[:-1])
    if v.endswith("ies") and len(v) > 3:
        out.add(v[:-3] + "y")
    return out


def _oracle_accepts(question: str, literals: Sequence[str]) -> bool:
    vocab = VOCAB | ({"districts", "name"} if literals else set())
    text = unicodedata.normalize("NFKC", question)
    lits = sorted(
        {unicodedata.normalize("NFKC", lit) for lit in literals}, key=lambda s: (-len(s), s)
    )
    mark = "\ue000"
    claimed: list[tuple[int, int]] = []
    for lit in lits:
        if not any(_oracle_word(c) for c in lit):
            return False
        hit = False
        for m in re.finditer(re.escape(lit), text):
            s, e = m.span()
            before = text[s - 1] if s > 0 else ""
            after = text[e] if e < len(text) else ""
            if (before and _oracle_word(before)) or (after and _oracle_word(after)):
                continue
            if any(s < ce and cs < e for cs, ce in claimed):
                continue
            claimed.append((s, e))
            hit = True
        if not hit:
            return False
    out: list[str] = []
    last = 0
    for s, e in sorted(claimed):
        out.append(text[last:s])
        out.append(mark)
        last = e
    out.append(text[last:])
    text = "".join(out)
    stripped = text.rstrip(" \t\n\r\x0b\x0c")
    if stripped.endswith("!"):
        text = stripped[:-1]
    # classify every character, then group runs
    def kind(ch: str) -> str:
        if ch == mark:
            return "S"
        if unicodedata.category(ch) in ("Zs", "Zl", "Zp") or ch in "\t\n\r\x0b\x0c" or ch in "?.,":
            return "_"
        return "W" if _oracle_word(ch) else "X"

    items: list[tuple[str, str]] = []
    for k, run in groupby(text, key=kind):
        chars = "".join(run)
        if k == "_":
            continue
        if k == "S":
            items.extend(("S", "") for _ in chars)
        else:
            items.append((k, chars))
    words: list[str | None] = []
    for n, (k, chars) in enumerate(items):
        if k != "W":
            words.append(None)
        elif chars.isascii() and chars.isalpha() and chars.islower():
            words.append(chars)
        elif n == 0 and chars.isascii() and chars.isalpha() and chars[0].isupper() and (
            len(chars) == 1 or chars[1:].islower()
        ):
            words.append(chars.lower())
        else:
            words.append(None)
    done = [k == "S" for k, _ in items]
    i = 0
    while i < len(items):
        if i + 1 < len(items) and (words[i], words[i + 1]) in LEADING:
            done[i] = done[i + 1] = True
            i += 2
        elif words[i] == "please":
            done[i] = True
            i += 1
        else:
            break
    for i in range(len(items) - 1):
        if (words[i], words[i + 1]) == ("number", "of"):
            done[i] = done[i + 1] = True
    for i, (k, chars) in enumerate(items):
        if done[i]:
            continue
        w = words[i]
        if k != "W" or w is None:
            return False
        if w in FRAME or w in CUES:
            continue
        lowered_first = i == 0 and w != chars
        if w in ("by", "per", "each", "every"):
            nxt = words[i + 1] if i + 1 < len(items) else None
            if nxt is not None and any(
                nxt == g or nxt in (g + "s", g[:-1] + "ies") for g in GROUP_VOCAB
            ):
                continue
            return False
        if any(w == v or (not lowered_first and w in _oracle_forms(v)) for v in vocab):
            continue
        return False
    return True


ALPHABET = (
    list("abcdefghijklmnopqrstuvwxyz") * 2
    + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("0123456789")
    + list("   ")
    + list("?.,!")
    + list("-_'\"`$&*+/\\|<>=~^%@#;:()[]{}")
    + [CJK[0], TAMIL[0], EMOJI, N_TILDE_ORTH[0], FULLWIDTH_TWO, ZERO_WIDTH, GREEK[0], ARABIC[0]]
)
GOOD_TOKENS = sorted(FRAME | CUES | VOCAB | {"districts", "name", "number", "of"}) + [
    "show me",
    "tell me",
    "can you",
]
BAD_TOKENS = [
    "US", "OR", "IT", "IN", "A", "us", "me", "it", "Wills", "Doe", "Cans", "wills", "cans", "3",
    "-2", "1.5", "2024-05-01", "North", "north", "South", "district", "by", "per", "each",
    CJK, TAMIL, EMOJI, "THE", "The", "Of", "ofs", "schoolss", "school", "Schools",
]  # fmt: skip
PLAN_LITERALS = ["North", "South", "2", "-2", "1.5", "2024-01-05", "Kuala Lumpur", CJK, "A", "OR"]


def _random_question(rng: random.Random) -> tuple[str, list[str]]:
    mode = rng.random()
    literals = rng.sample(PLAN_LITERALS, rng.choice([0, 0, 1, 1, 2]))
    if mode < 0.35:
        text = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 40)))
    else:
        parts = []
        for _ in range(rng.randint(2, 9)):
            roll = rng.random()
            if roll < 0.62:
                parts.append(rng.choice(GOOD_TOKENS))
            elif roll < 0.8 and literals:
                parts.append(rng.choice(literals))
            else:
                parts.append(rng.choice(BAD_TOKENS))
        text = " ".join(parts)
        if rng.random() < 0.5:
            text = text.capitalize()
        text += rng.choice(["", "", "?", ".", "!", " ?", "!!"])
    return text, literals


def _gate_accepts(question: str, literals: Sequence[str]) -> bool:
    flt = [("bronze.public_districts", "name", "=", lit) for lit in literals]
    got = mu.account_for_question(
        question, measure="schools_budget_sum", grain="bronze.public_schools", filters=flt
    )
    return got.ok


def test_hostile_alphabet_the_gate_accepts_only_what_the_oracle_accounts_for() -> None:
    rng = random.Random(20261015)
    accepted = refused = 0
    for _ in range(4000):
        text, literals = _random_question(rng)
        # the oracle's vocabulary is the plan's names; the gate sees the same plan
        got = _gate_accepts(text, literals)
        want = _oracle_accepts(text, literals)
        assert got == want, f"gate={got} oracle={want} for {text!r} literals={literals!r}"
        accepted += got
        refused += not got
    assert accepted >= 150 and refused >= 1500, (accepted, refused)


def _accepted_questions(count: int = 4000) -> Iterator[tuple[str, list[str]]]:
    rng = random.Random(7)
    for _ in range(count):
        text, literals = _random_question(rng)
        if _gate_accepts(text, literals):
            yield text, literals


def test_hostile_alphabet_what_the_gate_accepts_holds_four_direct_properties() -> None:
    """Independent of any oracle: whatever the gate accepted, once the plan's literals are cut
    out, has no digit, no non-ASCII character, no symbol, and no uppercase letter except the
    very first character of the question."""
    seen = 0
    for text, literals in _accepted_questions():
        seen += 1
        rest = unicodedata.normalize("NFKC", text)
        for lit in sorted({unicodedata.normalize("NFKC", lit) for lit in literals}, key=len)[::-1]:
            rest = rest.replace(lit, " ")
        rest = rest.rstrip()
        if rest.endswith("!"):
            rest = rest[:-1]
        assert rest.isascii(), (text, literals)
        assert not any(c.isdigit() for c in rest), (text, literals)
        assert all(c.isalpha() or c.isspace() or c in "?.," for c in rest), (text, literals)
        stripped = rest.lstrip()
        assert not any(c.isupper() for c in stripped[1:]), (text, literals)
    assert seen >= 150


def test_hostile_alphabet_every_literal_the_plan_carries_is_in_the_question() -> None:
    for text, literals in _accepted_questions():
        norm = unicodedata.normalize("NFKC", text)
        for lit in literals:
            assert unicodedata.normalize("NFKC", lit) in norm, (text, literals)


# ===================================================================== mutations
#
# Each mutation loosens ONE rule of the gate in place. The named check must then fail: if it
# still passed, the check would not be guarding the rule. (The same was done by hand on the
# source and reported with the commit.)


def _red(check: Callable[[], None]) -> bool:
    try:
        check()
    except AssertionError:
        return True
    return False


def _check_uppercase_frame_words_are_not_frame_words() -> None:
    for word in ("IN", "A", "OR", "THE", "OF"):
        assert not _fit(f"{Q} in {word}?").ok, word
    assert not _fit("WHAT is the total budget of schools").ok


def _check_a_lowercase_plural_is_not_a_frame_word() -> None:
    for word in ("cans", "lists", "gets", "wills", "thes", "ofs"):
        assert not _fit(f"{Q} of {word}").ok, word


def _check_other_scripts_are_conditions() -> None:
    for text in (CJK, TAMIL, EMOJI, FULLWIDTH_TWO, N_TILDE_ORTH, ARABIC, ZERO_WIDTH):
        assert not _fit(f"{Q} in {text}?").ok, text


def _check_an_unstated_literal_is_refused() -> None:
    assert not _fit(f"{Q}?", (DISTRICTS, "name", "=", "South")).ok
    assert not _fit(f"{Q} in the North district?", (DISTRICTS, "name", "IN", ["North", "Z"])).ok


def _check_punctuation_is_a_closed_set() -> None:
    for mark in ("-", "'", '"', "`", "$", "&", "*", "/", "_", "(", ";"):
        assert not _fit(f"{Q} {mark} x?").ok, mark
        assert not _fit(f"{Q} {mark}?").ok, mark


def _check_only_equals_and_in_are_stated() -> None:
    assert not _fit(f"{Q} in district 2?", (SCHOOLS, "district_id", ">", 2)).ok


def _check_the_first_token_alone_may_be_capitalised() -> None:
    assert not _fit("what is the Total budget of schools").ok
    assert not _fit("What is the total budget of Schools").ok


def _check_by_needs_a_grouping() -> None:
    assert not _fit(f"{Q} by schools?").ok
    assert not _fit(f"{Q} per school?").ok


def _check_a_literal_must_sit_on_token_boundaries() -> None:
    assert not _fit(f"{Q} in Northern?", (DISTRICTS, "name", "=", "North")).ok
    assert not _fit(f"{Q} in district 25?", (SCHOOLS, "district_id", "=", 5)).ok
    assert not _fit(f"{Q} in district -2?", (SCHOOLS, "district_id", "=", 2)).ok
    # "t" is not stated by the "t" inside "at": the leftover "a" is a frame word
    assert not _fit(f"{Q} at", (SCHOOLS, "grade", "=", "t")).ok
    assert _fit(f"{Q} at t", (SCHOOLS, "grade", "=", "t")).ok


def _check_a_longer_literal_claims_first() -> None:
    assert not _fit(
        f"{Q} in district 2.5?", (SCHOOLS, "district_id", "IN", [2.5, 5])
    ).ok


MUTATION_CHECKS: dict[str, Callable[[], None]] = {
    "frame-case-insensitive": _check_uppercase_frame_words_are_not_frame_words,
    "frame-stemmed": _check_a_lowercase_plural_is_not_a_frame_word,
    "non-ascii-allowed": _check_other_scripts_are_conditions,
    "literal-span-check-skipped": _check_an_unstated_literal_is_refused,
    "punctuation-open": _check_punctuation_is_a_closed_set,
    "operators-open": _check_only_equals_and_in_are_stated,
    "first-token-rule-open": _check_the_first_token_alone_may_be_capitalised,
    "breakdown-uncovered": _check_by_needs_a_grouping,
    "no-token-boundaries": _check_a_literal_must_sit_on_token_boundaries,
    "overlap-allowed": _check_a_longer_literal_claims_first,
}


@pytest.mark.parametrize("name", sorted(MUTATION_CHECKS))
def test_mutation_the_intact_gate_passes_the_check(name: str) -> None:
    MUTATION_CHECKS[name]()


def _patch(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Loosen exactly one rule of the gate."""
    real_plain = mu._plain
    real_claim = mu._claim_literals
    real_items = mu._items

    if name == "frame-case-insensitive":

        def plain(item: mu._Item, *, first: bool) -> str | None:
            return item.text.lower() if item.kind == "w" and item.text.isascii() else None

        monkeypatch.setattr(mu, "_plain", plain)
    elif name == "frame-stemmed":
        stemmed = frozenset(w for f in mu.FRAME_WORDS for w in (f, f + "s"))
        monkeypatch.setattr(mu, "FRAME_WORDS", stemmed)
    elif name == "non-ascii-allowed":

        def items(text: str, spans: Sequence[tuple[int, int]]) -> list[mu._Item]:
            return [i for i in real_items(text, spans) if i.text.isascii()]

        monkeypatch.setattr(mu, "_items", items)
    elif name == "literal-span-check-skipped":

        def claim(text: str, literals: Any) -> tuple[list[tuple[int, int]], list[str]]:
            spans, _missing = real_claim(text, literals)
            return spans, []

        monkeypatch.setattr(mu, "_claim_literals", claim)
    elif name == "punctuation-open":
        monkeypatch.setattr(mu, "_SEPARATOR_PUNCT", frozenset("?.,-'\"`$&*/_(;"))
    elif name == "operators-open":
        monkeypatch.setattr(mu, "_STATED_OPERATORS", frozenset({"=", "IN", ">", "<", "LIKE"}))
    elif name == "first-token-rule-open":

        def plain(item: mu._Item, *, first: bool) -> str | None:
            return real_plain(item, first=True)

        monkeypatch.setattr(mu, "_plain", plain)
    elif name == "breakdown-uncovered":
        monkeypatch.setattr(mu, "BREAKDOWN_WORDS", frozenset())
        monkeypatch.setattr(mu, "FRAME_WORDS", mu.FRAME_WORDS | {"by", "per", "each", "every"})
    elif name == "no-token-boundaries":

        def claim(text: str, literals: Any) -> tuple[list[tuple[int, int]], list[str]]:
            claimed: list[tuple[int, int]] = []
            missing: list[str] = []
            for lit in sorted(set(literals), key=lambda s: (-len(s), s)):
                at = text.find(lit)
                if at < 0:
                    missing.append(lit)
                else:
                    claimed.append((at, at + len(lit)))
            return sorted(claimed), missing

        monkeypatch.setattr(mu, "_claim_literals", claim)
    elif name == "overlap-allowed":

        def claim(text: str, literals: Any) -> tuple[list[tuple[int, int]], list[str]]:
            spans, missing = real_claim(text, literals)
            # every literal is "found" if it occurs anywhere, claimed or not
            return spans, [lit for lit in missing if lit not in text]

        monkeypatch.setattr(mu, "_claim_literals", claim)
    else:  # pragma: no cover
        raise AssertionError(name)


@pytest.mark.parametrize("name", sorted(MUTATION_CHECKS))
def test_mutation_loosening_one_rule_turns_its_named_check_red(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _patch(monkeypatch, name)
    assert _red(MUTATION_CHECKS[name]), f"loosening {name!r} did not fail its check"


def test_mutation_the_whole_repro_table_goes_red_when_the_gate_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mu, "account_for_question", lambda *a, **k: mu.Accounting(""))
    assert _red(_repro_gate_refuses)
    # ... and the envelope no longer refuses: the unfiltered total would be stated
    assert mu.measure_fit(f"{Q} in the US?", POOL, filters=[]).ok is True


def test_mutation_the_battery_goes_red_when_one_class_of_suffix_is_let_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_items = mu._items
    monkeypatch.setattr(
        mu, "_items", lambda t, s: [i for i in real_items(t, s) if i.text not in {"US", "OR", "IT"}]
    )
    assert _battery_failures()
