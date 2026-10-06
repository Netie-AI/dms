"""The workbook+sheet lane answers exactly two shapes; a clause it would ignore must abstain.

Red team (2026-10-02, Refs the Plan C report): the lane served ``L0_CERTIFIED`` plain SUMs for
"excluding Electronics", "the lowest", "by average", "as a percentage of the total", "by number of
SKUs", "top 1 SKU per category" and "... for sku SKU-3" on a top-N ask, because it only read
``top N`` + a category noun + a measure and ignored every other word. Each of those is a wrong
number under the strongest badge, so the lane now abstains by name instead.

Second-model review (Refs #372): a cue-word denylist on the prefix still served L0 for every
clause it forgot ("only Electronics", "this year", "annually", "before returns", "paling rendah").
The lane now serves only a question that parses whole into its grammar; any other span abstains
and is quoted back.

Asserted on the customer envelope (hard rule 10a): badge, abstained, rows, text.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from dms_executor.bronze import bronze_table_for_sheet
from dms_executor.bronze_sheet_ask import maybe_bronze_sheet_ask
from dms_executor.envelope import assert_envelope_valid
from dms_executor.lake_schema import ensure_lake_schemas

WB = "cf98e431_p50_01_sales_messy.xlsx"
SPACE = "cccccccc-cccc-cccc-cccc-cccccccccccc"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "wh.duckdb"
    ident = bronze_table_for_sheet(WB, "Sales").split(".", 1)[-1]
    con = duckdb.connect(str(path))
    try:
        ensure_lake_schemas(con)
        con.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        con.execute(
            f'CREATE TABLE bronze."{ident}" '
            "(category VARCHAR, sku VARCHAR, city VARCHAR, sales_value_myr DOUBLE)"
        )
        con.execute(
            f"""
            INSERT INTO bronze."{ident}" VALUES
              ('Electronics', 'SKU-1', 'Kuala Lumpur', 1500.0),
              ('Electronics', 'SKU-2', 'Johor Bahru', 500.0),
              ('Home', 'SKU-3', 'Kuala Lumpur', 900.0),
              ('Sports', 'SKU-4', 'Johor Bahru', 300.0),
              ('Misc', 'SKU-5', 'Kuala Lumpur', 100.0)
            """
        )
        wide = bronze_table_for_sheet(WB, "Wide_Fill").split(".", 1)[-1]
        con.execute(f'CREATE TABLE bronze."{wide}" (category VARCHAR, sales_value_myr DOUBLE)')
    finally:
        con.close()
    return path


def _ask_raw(db: Path, question: str) -> dict:
    env = maybe_bronze_sheet_ask(question, warehouse=db, space_id=SPACE)
    assert env is not None
    return env


def _ask(db: Path, tail: str) -> dict:
    return _ask_raw(db, f"In {WB} on the Sales sheet, {tail}")


def _reason(env: dict) -> str:
    return next(a for a in env["assumptions"] if "bronze_sheet_unhonored:" in a)


# tail -> the span the abstain must quote back. Each carries a clause the lane would drop.
UNHONORED: dict[str, tuple[str, str]] = {
    # Plan C red team
    "exclusion": (
        "what are the top 3 categories by sales_value_myr, excluding Electronics?",
        "excluding Electronics",
    ),
    "exclusion-prefix": (
        "excluding Electronics, what are the top 3 categories by sales_value_myr?",
        "excluding Electronics",
    ),
    "exclusion-gloss": (
        "what are the top 3 categories by sales_value_myr (excluding Electronics)?",
        "(excluding Electronics)",
    ),
    "direction": (
        "show the top 3 categories with the lowest sales_value_myr",
        "top 3 categories with the lowest sales_value_myr",
    ),
    "average": (
        "what are the top 3 categories by average sales_value_myr?",
        "top 3 categories by average sales_value_myr",
    ),
    "percentage": (
        "what are the top 3 categories by sales_value_myr, as a percentage of the total?",
        "as a percentage of the total",
    ),
    "count-measure": (
        "what are the top 2 categories by number of SKUs?",
        "top 2 categories by number of SKUs",
    ),
    "grain": (
        "what is the top 1 SKU per category by sales_value_myr?",
        "top 1 SKU per category by sales_value_myr",
    ),
    "extra-filter": (
        "what are the top 3 categories by sales_value_myr for sku SKU-3?",
        "for sku SKU-3",
    ),
    "time": ("what are the top 3 categories by sales_value_myr in 2025?", "in 2025"),
    "total-excluding": (
        "what is total sales_value_myr, excluding returns, for sku SKU-1?",
        "total sales_value_myr, excluding returns, for sku SKU-1",
    ),
    # Grok 4.7 review of 1f79ab2a: served L0 under the cue list
    "only-prefix": (
        "only Electronics, what are the top 3 categories by sales_value_myr?",
        "only Electronics",
    ),
    "only-paren": (
        "(only Electronics) what are the top 3 categories by sales_value_myr?",
        "(only Electronics)",
    ),
    "this-year": ("this year, what are the top 3 categories by sales_value_myr?", "this year"),
    "annually": ("annually, what are the top 3 categories by sales_value_myr?", "annually"),
    "just-home": ("just Home, what are the top 3 categories by sales_value_myr?", "just Home"),
    "before-returns": (
        "before returns, what is total sales_value_myr for sku SKU-1?",
        "before returns",
    ),
    "net-of-tax": ("net of tax, what is total sales_value_myr for sku SKU-1?", "net of tax"),
    "malay-lowest": (
        "paling rendah, apakah 3 kategori teratas mengikut sales_value_myr?",
        "paling rendah",
    ),
    "total-tail": (
        "what is total sales_value_myr for sku SKU-1 excluding returns?",
        "excluding returns",
    ),
    # adversarial shapes of our own
    "malay-tail-exclusion": (
        "top 3 categories by sales_value_myr kecuali Electronics?",
        "kecuali Electronics",
    ),
    "chinese-prefix": (
        "不包括 Electronics, what are the top 3 categories by sales_value_myr?",
        "不包括 Electronics",
    ),
    "quarter-window": (
        "for Q3 2025, what is total sales_value_myr for city Kuala Lumpur?",
        "for Q3 2025",
    ),
    "ytd-tail": ("what are the top 3 categories by sales_value_myr year to date?", "year to date"),
    "superlative-in-shape": (
        "what are the top 3 smallest categories by sales_value_myr?",
        "top 3 smallest categories by sales_value_myr",
    ),
    "ascending-tail": ("top 3 categories by sales_value_myr, ascending", "ascending"),
    "apart-from-prefix": (
        "apart from Home, show the top 3 categories by sales_value_myr",
        "apart from Home",
    ),
    "polite-negation": (
        "please don't include Electronics, show the top 3 categories by sales_value_myr",
        "please don't include Electronics",
    ),
    "units-prefix": (
        "in thousands of MYR, what are the top 3 categories by sales_value_myr?",
        "in thousands of MYR",
    ),
    "units-tail": ("what is total sales_value_myr for sku SKU-1 in USD?", "in USD"),
    "city-tail": (
        "what is total sales_value_myr for city Kuala Lumpur excluding returns?",
        "excluding returns",
    ),
    "grain-tail": ("top 3 categories by sales_value_myr per city", "per city"),
    "no-measure": ("what are the top 3 categories?", "top 3 categories"),
}


@pytest.mark.parametrize("tail", [t for t, _ in UNHONORED.values()], ids=list(UNHONORED))
def test_ignored_clause_abstains_by_name_not_a_plain_sum(db: Path, tail: str) -> None:
    env = _ask(db, tail)
    assert env["abstained"] is True, env["text"]
    assert env["badge"] == "ABSTAIN"
    assert env["route"] == "abstain"
    assert not env["rows"] and not env["values"]
    assert any("bronze_sheet_unhonored:" in a for a in env["assumptions"]), env["assumptions"]
    # no figure from the table may reach the customer
    for figure in ("1500", "2000", "900", "300", "100"):
        assert figure not in (env["text"] or ""), env["text"]
    assert_envelope_valid(env)


@pytest.mark.parametrize("case", list(UNHONORED.values()), ids=list(UNHONORED))
def test_the_abstain_quotes_the_unparsed_span(db: Path, case: tuple[str, str]) -> None:
    tail, span = case
    env = _ask(db, tail)
    assert f"bronze_sheet_unparsed:{span}" in env["assumptions"], env["assumptions"]
    assert span in env["text"], env["text"]


def test_the_abstain_names_the_grammar_slot(db: Path) -> None:
    slot = {name: _reason(_ask(db, tail)) for name, (tail, _) in UNHONORED.items()}
    assert slot["exclusion"] == "bronze_sheet_unhonored:suffix"
    assert slot["only-prefix"] == "bronze_sheet_unhonored:prefix"
    assert slot["grain"] == "bronze_sheet_unhonored:shape"


SCOPE_BROKEN = {
    "clause-before-workbook": (
        f"Only Electronics: in {WB} on the Sales sheet, what are the top 3 categories "
        "by sales_value_myr?"
    ),
    "clause-between-workbook-and-sheet": (
        f"In {WB} excluding Electronics on the Sales sheet, what are the top 3 categories "
        "by sales_value_myr?"
    ),
    "ignore-the-scoped-sheet": (
        f"In {WB}, on the Sales sheet only (ignore Sales), what are the top 3 categories "
        "by sales_value_myr?"
    ),
    # not a sheet of this workbook, so it is an exclusion the lane would drop
    "ignore-a-category": (
        f"Using {WB}, on the Sales sheet only (ignore Electronics), what are the top 3 "
        "categories by sales_value_myr?"
    ),
}


@pytest.mark.parametrize("question", list(SCOPE_BROKEN.values()), ids=list(SCOPE_BROKEN))
def test_unparsed_scope_abstains_by_name(db: Path, question: str) -> None:
    env = _ask_raw(db, question)
    assert env["abstained"] is True and env["badge"] == "ABSTAIN", env["text"]
    assert not env["rows"]
    assert _reason(env) == "bronze_sheet_unhonored:scope"
    assert_envelope_valid(env)


# The lane's own contract: these phrasings must keep answering with an L0 figure.
SUPPORTED = {
    "plain-top-n": "what are the top 3 categories by sales_value_myr?",
    "show": "show the top 5 categories by sales_value_myr.",
    "no-question-mark": "top 3 categories by sales_value_myr",
    "product-family-synonym": (
        "top 3 product families by MYR sales (cat / product line synonym for category)?"
    ),
    # harmless filler: politeness and request verbs that do not change the answer
    "please-lead": "please, what are the top 3 categories by sales_value_myr?",
    "show-me-please": "show me the top 3 categories by sales_value_myr, please.",
    "could-you-thanks": "could you please list the top 3 categories by sales_value_myr? thanks",
    "whats-the": "what's the top 3 categories by sales_value_myr?",
    "give-me": "give me the top 3 categories by sales_value_myr!",
    "tell-me": "tell me the top 3 categories by sales_value_myr",
}


@pytest.mark.parametrize("tail", list(SUPPORTED.values()), ids=list(SUPPORTED))
def test_supported_top_n_shapes_still_answer(db: Path, tail: str) -> None:
    env = _ask(db, tail)
    assert env["abstained"] is False, env["text"]
    assert env["badge"] == "L0_CERTIFIED"
    assert [r["category"] for r in env["rows"]][:3] == ["Electronics", "Home", "Sports"]
    assert env["rows"][0]["sales_value_myr"] == 2000.0
    assert "sales_value_myr=2000.0" in env["text"]
    assert_envelope_valid(env)


def test_sheet_only_ignore_other_sheet_still_answers(db: Path) -> None:
    env = _ask_raw(
        db,
        f"Using {WB}, on the Sales sheet only (ignore Wide_Fill), what are the top 3 "
        "categories by sales_value_myr?",
    )
    assert env["abstained"] is False, env["text"]
    assert [r["category"] for r in env["rows"]] == ["Electronics", "Home", "Sports"]
    assert_envelope_valid(env)


TOTAL_SUPPORTED = {
    "plain": "In {wb} on the Sales sheet, what is total sales_value_myr for city Kuala Lumpur?",
    "the-total": "In {wb} sheet Sales, what is the total sales_value_myr for city Kuala Lumpur?",
    "polite-tail": (
        "In {wb} sheet Sales, what is total sales_value_myr for city Kuala Lumpur, please?"
    ),
    "greeting": "Hi, in {wb} sheet Sales, can you show me total sales_value_myr for city "
    "Kuala Lumpur?",
}


@pytest.mark.parametrize("question", list(TOTAL_SUPPORTED.values()), ids=list(TOTAL_SUPPORTED))
def test_supported_total_shape_still_answers(db: Path, question: str) -> None:
    env = _ask_raw(db, question.format(wb=WB))
    assert env["abstained"] is False, env["text"]
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"] == [{"city": "Kuala Lumpur", "sales_value_myr": 2500.0}]
    assert "sales_value_myr=2500.0" in env["text"]
    assert_envelope_valid(env)


def test_quoted_sku_total_still_answers(db: Path) -> None:
    env = _ask(db, "what is total sales_value_myr for sku 'SKU-1'?")
    assert env["abstained"] is False, env["text"]
    assert env["rows"] == [{"sku": "SKU-1", "sales_value_myr": 1500.0}]


@pytest.mark.parametrize(
    "question",
    [
        f"Dalam fail {WB} helaian Sales, apakah 3 kategori teratas mengikut sales_value_myr?",
        f"Dalam fail {WB} helaian Sales, sila tunjukkan 3 kategori teratas mengikut "
        "sales_value_myr",
    ],
    ids=["apakah", "sila-tunjukkan"],
)
def test_malay_phrasing_still_answers(db: Path, question: str) -> None:
    env = maybe_bronze_sheet_ask(question, warehouse=db)
    assert env is not None and env["abstained"] is False, env and env["text"]
    assert [r["category"] for r in env["rows"]] == ["Electronics", "Home", "Sports"]


def test_empty_filter_result_still_abstains_via_rule_12(db: Path) -> None:
    env = _ask(db, "what is total sales_value_myr for sku SKU-X?")
    assert env["abstained"] is True
    assert env["badge"] == "ABSTAIN"


# Grok review of 9dc65d2c (PR #341): a second city word was bound into the exact filter, so a
# stored city equal to the glued pair served L0 and rule 12 (empty result only) never fired.
POISON = {"KL only": 7777.0, "KL not": 4242.0, "KL excluding": 3333.0, "Johor not": 8888.0}


@pytest.fixture()
def poisoned(db: Path) -> Path:
    ident = bronze_table_for_sheet(WB, "Sales").split(".", 1)[-1]
    con = duckdb.connect(str(db))
    try:
        rows = [*POISON.items(), ("KL", 111.0), ("Johor", 50.0), ("Shah Alam", 9000.0)]
        con.executemany(
            f"INSERT INTO bronze.\"{ident}\" VALUES ('Misc', 'SKU-9', ?, ?)",
            [list(r) for r in rows],
        )
    finally:
        con.close()
    return db


# tail -> (slot, span quoted back)
GLUED_CITY = {
    "kl-only-please": (
        "what is total sales_value_myr for city KL only please?",
        "suffix",
        "only please",
    ),
    "quoted-kl-only": (
        'what is total sales_value_myr for city "KL only"?',
        "shape",
        'total sales_value_myr for city "KL only"',
    ),
    "kl-not": ("what is total sales_value_myr for city KL not?", "suffix", "not"),
    "kl-excluding": (
        "what is total sales_value_myr for city KL excluding?",
        "suffix",
        "excluding",
    ),
    "johor-not": ("what is total sales_value_myr for city Johor not?", "suffix", "not"),
}


@pytest.mark.parametrize("case", list(GLUED_CITY.values()), ids=list(GLUED_CITY))
def test_glued_city_word_abstains_even_when_stored(
    poisoned: Path, case: tuple[str, str, str]
) -> None:
    tail, slot, span = case
    env = _ask(poisoned, tail)
    assert env["abstained"] is True, env["text"]
    assert env["badge"] == "ABSTAIN"
    assert env["route"] == "abstain"
    assert not env["rows"] and not env["values"]
    assert _reason(env) == f"bronze_sheet_unhonored:{slot}"
    assert f"bronze_sheet_unparsed:{span}" in env["assumptions"], env["assumptions"]
    assert "would ignore that clause" in env["text"]
    for figure in ("7777", "4242", "3333", "8888", "111", "50.0"):
        assert figure not in (env["text"] or ""), env["text"]
    assert_envelope_valid(env)


@pytest.mark.parametrize(
    ("tail", "city", "figure"),
    [
        ("what is total sales_value_myr for city KL?", "KL", 111.0),
        ("what is total sales_value_myr for city Shah Alam?", "Shah Alam", 9000.0),
        ('what is total sales_value_myr for city "Kuala Lumpur"?', "Kuala Lumpur", 2500.0),
    ],
    ids=["one-word", "listed-two-word", "quoted-listed"],
)
def test_recognised_city_still_answers_on_poisoned_sheet(
    poisoned: Path, tail: str, city: str, figure: float
) -> None:
    env = _ask(poisoned, tail)
    assert env["abstained"] is False, env["text"]
    assert env["badge"] == "L0_CERTIFIED"
    assert env["rows"] == [{"city": city, "sales_value_myr": figure}]
    assert f"sales_value_myr={figure}" in env["text"]
    assert_envelope_valid(env)


@pytest.mark.parametrize(
    "tail",
    [
        "what are the top ３ categories by sales_value_myr?",
        "what are the top ٣ categories by sales_value_myr?",
    ],
    ids=["fullwidth", "arabic-indic"],
)
def test_non_ascii_top_n_digit_abstains(db: Path, tail: str) -> None:
    env = _ask(db, tail)
    assert env["abstained"] is True, env["text"]
    assert not env["rows"] and not env["values"]
    assert _reason(env) == "bronze_sheet_unhonored:shape"
    assert "2000" not in (env["text"] or "")
    assert_envelope_valid(env)
