"""RANK-WORDS-02: rank, skip, and entity-exclusion words on the ask path.

Seed is the demo warehouse used by generative_ask / qualifiers tests.
Each phrasing below served a wrong L2 list (or an unnamed abstain) on
main 6f7139a3. Head either compiles the window or names the abstain.
Controls are the stable envelope fields from that same main sha.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from dms_executor.demo_ask import answer_demo_question
from dms_executor.demo_warehouse import ensure_demo_warehouse
from dms_executor.envelope import assert_envelope_valid
from dms_executor.generative_ask import load_verified_ontology, maybe_generative_ask

CONTROLS = {'demo': {'top 5 skus by revenue': {'badge': 'L2_VALIDATED',
                                    'abstained': False,
                                    'text': 'SKUs by outbound revenue ranks 1–5 — #1 SKU-BETA '
                                            'at RM 8,312.50.',
                                    'rows': [{'sku': 'SKU-BETA', 'revenue_myr': 8312.5},
                                             {'sku': 'SKU-ALPHA', 'revenue_myr': 5670.0},
                                             {'sku': 'RS622XK', 'revenue_myr': 3915.0},
                                             {'sku': 'SKU-EPSILON', 'revenue_myr': 2925.0},
                                             {'sku': 'SKU-GAMMA', 'revenue_myr': 2640.0}],
                                    'sql_used': 'SELECT sku, SUM(quantity_kg * '
                                                'unit_cost_myr)::DOUBLE AS revenue_myr FROM '
                                                "transactions WHERE txn_type = 'outbound' "
                                                'GROUP BY sku ORDER BY revenue_myr DESC, sku '
                                                'ASC LIMIT 5 OFFSET 0',
                                    'assumptions': ['outbound only', 'demo warehouse'],
                                    'values': [{'id': 'v_top',
                                                'value': 8312.5,
                                                'unit': 'MYR',
                                                'label': 'SKU-BETA revenue'},
                                               {'id': 'v1',
                                                'value': 5670.0,
                                                'label': 'revenue_myr'},
                                               {'id': 'v2',
                                                'value': 3915.0,
                                                'label': 'revenue_myr'},
                                               {'id': 'v3',
                                                'value': 2925.0,
                                                'label': 'revenue_myr'},
                                               {'id': 'v4',
                                                'value': 2640.0,
                                                'label': 'revenue_myr'}],
                                    'chart': {'kind': 'hbar',
                                              'x': 'sku',
                                              'y': 'revenue_myr',
                                              'title': 'SKU revenue ranks 1–5'}},
          'top 3 selling skus': {'badge': 'L2_VALIDATED',
                                 'abstained': False,
                                 'text': 'SKUs by outbound revenue ranks 1–3 — #1 SKU-BETA at '
                                         'RM 8,312.50.',
                                 'rows': [{'sku': 'SKU-BETA', 'revenue_myr': 8312.5},
                                          {'sku': 'SKU-ALPHA', 'revenue_myr': 5670.0},
                                          {'sku': 'RS622XK', 'revenue_myr': 3915.0}],
                                 'sql_used': 'SELECT sku, SUM(quantity_kg * '
                                             'unit_cost_myr)::DOUBLE AS revenue_myr FROM '
                                             "transactions WHERE txn_type = 'outbound' GROUP "
                                             'BY sku ORDER BY revenue_myr DESC, sku ASC LIMIT '
                                             '3 OFFSET 0',
                                 'assumptions': ['outbound only', 'demo warehouse'],
                                 'values': [{'id': 'v_top',
                                             'value': 8312.5,
                                             'unit': 'MYR',
                                             'label': 'SKU-BETA revenue'},
                                            {'id': 'v1',
                                             'value': 5670.0,
                                             'label': 'revenue_myr'},
                                            {'id': 'v2',
                                             'value': 3915.0,
                                             'label': 'revenue_myr'}],
                                 'chart': {'kind': 'hbar',
                                           'x': 'sku',
                                           'y': 'revenue_myr',
                                           'title': 'SKU revenue ranks 1–3'}}},
 'gen': {'top 5 skus by revenue': {'badge': 'L2_VALIDATED',
                                   'abstained': False,
                                   'text': 'Found 5 row(s).\n'
                                           '  - product_sku=SKU-BETA, '
                                           'outbound_value_myr=8312.5\n'
                                           '  - product_sku=SKU-ALPHA, '
                                           'outbound_value_myr=5670.0\n'
                                           '  - product_sku=RS622XK, '
                                           'outbound_value_myr=3915.0\n'
                                           '  - product_sku=SKU-EPSILON, '
                                           'outbound_value_myr=2925.0\n'
                                           '  - product_sku=SKU-GAMMA, '
                                           'outbound_value_myr=2640.0',
                                   'rows': [{'product_sku': 'SKU-BETA',
                                             'outbound_value_myr': 8312.5},
                                            {'product_sku': 'SKU-ALPHA',
                                             'outbound_value_myr': 5670.0},
                                            {'product_sku': 'RS622XK',
                                             'outbound_value_myr': 3915.0},
                                            {'product_sku': 'SKU-EPSILON',
                                             'outbound_value_myr': 2925.0},
                                            {'product_sku': 'SKU-GAMMA',
                                             'outbound_value_myr': 2640.0}],
                                   'sql_used': 'SELECT d0."sku" AS "product_sku", '
                                               "ROUND(SUM(CASE WHEN f.txn_type IN ('OUT', "
                                               "'outbound') THEN f.quantity_kg * "
                                               'f.unit_cost_myr ELSE 0 END), 2) AS '
                                               '"outbound_value_myr"\n'
                                               'FROM (SELECT *, CAST(ts AS DATE) AS day FROM '
                                               'transactions) f\n'
                                               'LEFT JOIN (SELECT sku, ANY_VALUE(category) AS '
                                               'category FROM inventory GROUP BY sku) d0 ON '
                                               'f."sku" = d0."sku"\n'
                                               'GROUP BY d0."sku"\n'
                                               'ORDER BY "outbound_value_myr" DESC\n'
                                               'LIMIT 5',
                                   'assumptions': ['GEN-01 ontology compile',
                                                   'executed via Cortex submit after validate',
                                                   'joined product through txn_of_product '
                                                   '(verified many-to-one, so no fact row is '
                                                   'duplicated)',
                                                   'compute_fallback:bind_plan',
                                                   'include: grain=transaction; '
                                                   'measure=outbound_value_myr; outbound '
                                                   'issued stock value at cost (revenue / '
                                                   'sales); one contribution per transaction; '
                                                   'group product.sku; txn_type IN (OUT, '
                                                   'outbound)',
                                                   'exclude: missing groups not zero-padded; '
                                                   'txn_type not in (OUT, outbound); rows '
                                                   'beyond LIMIT 5 not returned',
                                                   'unsure: LEFT JOIN unmatched dim keys stay '
                                                   'in the total (not inner-dropped)'],
                                   'values': [{'id': 'v0',
                                               'value': 8312.5,
                                               'label': 'outbound_value_myr'},
                                              {'id': 'v1',
                                               'value': 5670.0,
                                               'label': 'outbound_value_myr'},
                                              {'id': 'v2',
                                               'value': 3915.0,
                                               'label': 'outbound_value_myr'},
                                              {'id': 'v3',
                                               'value': 2925.0,
                                               'label': 'outbound_value_myr'},
                                              {'id': 'v4',
                                               'value': 2640.0,
                                               'label': 'outbound_value_myr'}],
                                   'chart': {'kind': 'hbar',
                                             'x': 'product_sku',
                                             'y': 'outbound_value_myr',
                                             'title': 'Result'}},
         'top 3 selling skus': {'badge': 'L2_VALIDATED',
                                'abstained': False,
                                'text': 'Found 3 row(s).\n'
                                        '  - product_sku=SKU-BETA, outbound_value_myr=8312.5\n'
                                        '  - product_sku=SKU-ALPHA, outbound_value_myr=5670.0\n'
                                        '  - product_sku=RS622XK, outbound_value_myr=3915.0',
                                'rows': [{'product_sku': 'SKU-BETA',
                                          'outbound_value_myr': 8312.5},
                                         {'product_sku': 'SKU-ALPHA',
                                          'outbound_value_myr': 5670.0},
                                         {'product_sku': 'RS622XK',
                                          'outbound_value_myr': 3915.0}],
                                'sql_used': 'SELECT d0."sku" AS "product_sku", ROUND(SUM(CASE '
                                            "WHEN f.txn_type IN ('OUT', 'outbound') THEN "
                                            'f.quantity_kg * f.unit_cost_myr ELSE 0 END), 2) '
                                            'AS "outbound_value_myr"\n'
                                            'FROM (SELECT *, CAST(ts AS DATE) AS day FROM '
                                            'transactions) f\n'
                                            'LEFT JOIN (SELECT sku, ANY_VALUE(category) AS '
                                            'category FROM inventory GROUP BY sku) d0 ON '
                                            'f."sku" = d0."sku"\n'
                                            'GROUP BY d0."sku"\n'
                                            'ORDER BY "outbound_value_myr" DESC\n'
                                            'LIMIT 3',
                                'assumptions': ['GEN-01 ontology compile',
                                                'executed via Cortex submit after validate',
                                                'joined product through txn_of_product '
                                                '(verified many-to-one, so no fact row is '
                                                'duplicated)',
                                                'compute_fallback:bind_plan',
                                                'include: grain=transaction; '
                                                'measure=outbound_value_myr; outbound issued '
                                                'stock value at cost (revenue / sales); one '
                                                'contribution per transaction; group '
                                                'product.sku; txn_type IN (OUT, outbound)',
                                                'exclude: missing groups not zero-padded; '
                                                'txn_type not in (OUT, outbound); rows beyond '
                                                'LIMIT 3 not returned',
                                                'unsure: LEFT JOIN unmatched dim keys stay in '
                                                'the total (not inner-dropped)'],
                                'values': [{'id': 'v0',
                                            'value': 8312.5,
                                            'label': 'outbound_value_myr'},
                                           {'id': 'v1',
                                            'value': 5670.0,
                                            'label': 'outbound_value_myr'},
                                           {'id': 'v2',
                                            'value': 3915.0,
                                            'label': 'outbound_value_myr'}],
                                'chart': {'kind': 'hbar',
                                          'x': 'product_sku',
                                          'y': 'outbound_value_myr',
                                          'title': 'Result'}}}}
_STABLE = (
    "badge",
    "abstained",
    "text",
    "rows",
    "sql_used",
    "assumptions",
    "values",
    "chart",
)

# phrase, named reason that must appear on abstain, SQL fragments if L2 instead.
# A validated exclusion must be the WHERE, not an abstain.
_WINDOWS = [
    (
        "skip the first 3 skus and show the next 5 by revenue",
        "unhonored_qualifier:rank_window=offset=3,limit=5",
        ("OFFSET 3", "LIMIT 5"),
    ),
    (
        "skipping the first three skus, show the next five by revenue",
        "unhonored_qualifier:rank_window=offset=3,limit=5",
        ("OFFSET 3", "LIMIT 5"),
    ),
    (
        "after the first 3 skus list the next 5 by revenue",
        "unhonored_qualifier:rank_window=offset=3,limit=5",
        ("OFFSET 3", "LIMIT 5"),
    ),
    (
        "bottom 5 excluding bottom 3 skus by revenue",
        "unhonored_qualifier:rank_window=asc,offset=3,limit=2",
        ("OFFSET 3", "LIMIT 2"),
    ),
    (
        "the bottom five excluding the bottom three skus by revenue",
        "unhonored_qualifier:rank_window=asc,offset=3,limit=2",
        ("OFFSET 3", "LIMIT 2"),
    ),
    (
        "lowest 5 skus excluding the lowest 3 by revenue",
        "unhonored_qualifier:rank_window=asc,offset=3,limit=2",
        ("OFFSET 3", "LIMIT 2"),
    ),
    (
        "show ranks 8 to 4 skus by revenue",
        "rank_window_reversed",
        ("OFFSET 3", "LIMIT 5"),
    ),
    (
        "sku ranks 8 through 4 by revenue",
        "rank_window_reversed",
        ("OFFSET 3", "LIMIT 5"),
    ),
    (
        "positions 8 to 4 skus by revenue",
        "rank_window_reversed",
        ("OFFSET 3", "LIMIT 5"),
    ),
]

_VALID_EXCLUSIONS = [
    ("excluding SKU-ALPHA, top 5 selling skus by revenue", "SKU-ALPHA"),
    ("except for SKU-BETA, top 5 skus by revenue", "SKU-BETA"),
    ("without SKU-GAMMA, top 5 selling skus by revenue", "SKU-GAMMA"),
]

_INVALID_EXCLUSIONS = [
    ("excluding SKU-NOPE, top 5 selling skus by revenue", "SKU-NOPE"),
    ("except SKU-ZZZ, top 5 skus by revenue", "SKU-ZZZ"),
    ("ignoring SKU-MISSING, top 5 selling skus by revenue", "SKU-MISSING"),
]


def _stable(env: dict[str, Any]) -> dict[str, Any]:
    return {key: env.get(key) for key in _STABLE}


def _blob(env: dict[str, Any]) -> str:
    return (env.get("text") or "") + "\n" + "\n".join(env.get("assumptions") or [])


@pytest.fixture()
def warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "dms_demo.duckdb"
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(path))
    os.environ["DMS_WAREHOUSE_DB"] = str(path)
    from dms_executor import demo_warehouse as dw

    dw._SEEDED.clear()
    ensure_demo_warehouse(path)
    return path


def _submit(warehouse: Path):
    def submit(sql: str) -> Any:
        import duckdb

        con = duckdb.connect(str(warehouse), read_only=True)
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
        finally:
            con.close()

        class Result:
            ok = True
            status = "ok"
            run_id = "run_rank_words"
            output = {"rows": rows}

        return Result()

    return submit


def _ledger(_payload: dict[str, Any]) -> Any:
    class Ledger:
        entry_id = "led_rank_words"
        hash = "hash_rank_words_not_entry"

    return Ledger()


def _ask(question: str, warehouse: Path) -> dict[str, Any]:
    onto = load_verified_ontology(warehouse)
    assert onto is not None
    import duckdb

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        tables = {r[0] for r in con.execute("show tables").fetchall()}
    finally:
        con.close()
    env = maybe_generative_ask(
        question,
        warehouse=warehouse,
        grantable=tables,
        compute=lambda _ctx: {"ok": False, "status": "miss"},
        submit=_submit(warehouse),
        ledger_append=_ledger,
        ontology=onto,
        bind_on_miss=True,
    )
    assert env is not None
    assert_envelope_valid(env)
    return env


def _assert_window_or_named(env: dict[str, Any], reason: str, sql_bits: tuple[str, ...]) -> None:
    if env.get("abstained"):
        assert env["badge"] == "ABSTAIN"
        assert env["rows"] == []
        assert reason in _blob(env)
        return
    sql = str(env.get("sql_used") or "")
    for bit in sql_bits:
        assert bit in sql, sql
    assert env["badge"] == "L2_VALIDATED"
    assert env["rows"]


@pytest.mark.parametrize("question,reason,sql_bits", _WINDOWS)
def test_rank_window_words_are_honoured_or_named(
    warehouse: Path, question: str, reason: str, sql_bits: tuple[str, ...]
) -> None:
    env = _ask(question, warehouse)
    _assert_window_or_named(env, reason, sql_bits)
    # A silent top-of-list is the bug. Rank 1 of this warehouse is SKU-BETA.
    if env.get("abstained"):
        assert "SKU-BETA" not in (env.get("text") or "")


@pytest.mark.parametrize("question,sku", _VALID_EXCLUSIONS)
def test_validated_sku_exclusion_filters_top_5(
    warehouse: Path, question: str, sku: str
) -> None:
    env = _ask(question, warehouse)
    assert env["badge"] == "L2_VALIDATED"
    assert env["abstained"] is False
    sql = str(env.get("sql_used") or "")
    assert "LIMIT 5" in sql
    assert sku in sql
    assert ("<>" in sql) or ("NOT IN" in sql.upper())
    assert all(sku not in str(row.values()) for row in env["rows"])


@pytest.mark.parametrize("question,literal", _INVALID_EXCLUSIONS)
def test_unvalidated_sku_exclusion_is_named(
    warehouse: Path, question: str, literal: str
) -> None:
    env = _ask(question, warehouse)
    assert env["badge"] == "ABSTAIN"
    assert env["rows"] == []
    assert f"unhandled_exclusion:{literal}" in _blob(env)


@pytest.mark.parametrize("question", ["top 5 skus by revenue", "top 3 selling skus"])
def test_plain_top_n_generative_matches_main(warehouse: Path, question: str) -> None:
    env = _ask(question, warehouse)
    assert _stable(env) == CONTROLS["gen"][question]


@pytest.mark.parametrize("question", ["top 5 skus by revenue", "top 3 selling skus"])
def test_plain_top_n_demo_matches_main(warehouse: Path, question: str) -> None:
    env = answer_demo_question(question)
    assert_envelope_valid(env)
    assert _stable(env) == CONTROLS["demo"][question]


def test_demo_reversed_range_keeps_the_swapped_window(warehouse: Path) -> None:
    """Demo already compiled ranks 8 to 4 as OFFSET 3 LIMIT 5. Leave that answer."""
    env = answer_demo_question("show ranks 8 to 4 skus by revenue")
    assert_envelope_valid(env)
    assert env["badge"] == "L2_VALIDATED"
    sql = str(env.get("sql_used") or "")
    assert "OFFSET 3" in sql
    assert "LIMIT 5" in sql
    assert env["rows"][0]["sku"] == "SKU-EPSILON"


def test_forward_rank_range_stays_a_plain_top_list(warehouse: Path) -> None:
    """Forward 'ranks 4 to 8' is the other lane. This guard must not abstain it."""
    env = _ask("show ranks 4 to 8 skus by revenue", warehouse)
    assert env["badge"] == "L2_VALIDATED"
    assert "rank_window_reversed" not in _blob(env)
    assert "OFFSET" not in str(env.get("sql_used") or "")
