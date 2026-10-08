"""VALUE-HINT-SPAN-01: value hints are looked up on the masker's output.

A partial or full person name in the question must not seed a hint on a
mask-cleared column. Synthetic names only. No word list in the product.
"""

from __future__ import annotations

from typing import Any

from dms_core.pii import mask_payload, mask_personal_spans
from dms_executor.schema_context import build_schema_context

# Human questions. Each stored list is the name and its pieces, so the
# raw-question matcher on f433e9c9 emits a hint. The masked matcher must not.
_NAME_QUESTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("how many orders did nora voss place", ("nora voss", "nora", "voss")),
    ("show NORA VOSS this week", ("NORA VOSS", "NORA", "VOSS")),
    ("what did Nora Voss buy", ("Nora Voss", "Nora", "Voss")),
    ("revenue for siti aminah last quarter", ("siti aminah", "siti", "aminah")),
    ("list sales by SITI AMINAH", ("SITI AMINAH", "SITI", "AMINAH")),
    ("open the book for Siti Aminah", ("Siti Aminah", "Siti", "Aminah")),
    ("orders placed by ahmad bin ali", ("ahmad bin ali", "ahmad", "bin", "ali")),
    ("AHMAD BIN ALI called about a shipment", ("AHMAD BIN ALI", "AHMAD", "BIN", "ALI")),
    ("follow up with Ahmad Bin Ali", ("Ahmad Bin Ali", "Ahmad", "Bin", "Ali")),
    ("did mina cole confirm", ("mina cole", "mina", "cole")),
    ("MINA COLE sent the file", ("MINA COLE", "MINA", "COLE")),
    ("ask Mina Cole for the total", ("Mina Cole", "Mina", "Cole")),
    ("jon pell is waiting", ("jon pell", "jon", "pell")),
    ("JON PELL asked again", ("JON PELL", "JON", "PELL")),
    ("pass this to Jon Pell", ("Jon Pell", "Jon", "Pell")),
    ("ruth hale needs the figure", ("ruth hale", "ruth", "hale")),
    ("RUTH HALE owns the account", ("RUTH HALE", "RUTH", "HALE")),
    ("check with Ruth Hale", ("Ruth Hale", "Ruth", "Hale")),
    ("ada quinn logged in", ("ada quinn", "ada", "quinn")),
    ("ADA QUINN updated the row", ("ADA QUINN", "ADA", "QUINN")),
    ("mail Ada Quinn the summary", ("Ada Quinn", "Ada", "Quinn")),
    ("lee tan approved it", ("lee tan", "lee", "tan")),
    ("LEE TAN signed off", ("LEE TAN", "LEE", "TAN")),
    ("call Lee Tan today", ("Lee Tan", "Lee", "Tan")),
    ("how many orders did nora voss, place", ("nora voss", "nora", "voss")),
    ("show NORA VOSS.", ("NORA VOSS", "NORA", "VOSS")),
    ("what did Nora Voss?", ("Nora Voss", "Nora", "Voss")),
    ("orders for siti aminah binti", ("siti aminah binti", "siti", "aminah", "binti")),
    ("SITI AMINAH BINTI is the contact", ("SITI AMINAH BINTI", "SITI", "AMINAH", "BINTI")),
    ("reach Siti Aminah Binti.", ("Siti Aminah Binti", "Siti", "Aminah", "Binti")),
    ("lookup nora voss in the ledger", ("voss",)),
    ("sales attributed to ahmad bin ali", ("ahmad",)),
)


def _column(name: str, values: list[str], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "type": "varchar",
        "distinct": len(values),
        "values": values,
    }
    body.update(extra)
    return body


def _prompt(question: str, columns: list[dict[str, Any]], *, table: str = "item") -> str:
    return build_schema_context(
        question,
        {"dialect": "mysql", "datasets": [{"name": table, "columns": columns}]},
    ).prompt


def _emitted_hints(prompt: str) -> list[str]:
    lines = prompt.splitlines()
    if "FILTER HINTS" not in lines:
        return []
    emitted: list[str] = []
    for line in lines[lines.index("FILTER HINTS") + 1 :]:
        if not line.startswith("- "):
            break
        if " = " in line:
            emitted.append(line.split(" = ", 1)[1])
        elif " ~ " in line:
            emitted.append(line.split(" ~ ", 1)[1])
    return emitted


def test_name_questions_are_at_least_thirty() -> None:
    assert len(_NAME_QUESTIONS) >= 30


def test_mask_cleared_column_does_not_hint_a_person_span() -> None:
    for question, parts in _NAME_QUESTIONS:
        prompt = _prompt(question, [_column("category", list(parts))])
        emitted = _emitted_hints(prompt)
        for part in parts:
            assert part not in emitted, (question, part, emitted)
            assert all(part not in hint for hint in emitted), (question, part, emitted)


def test_compare_chemicals_and_solvents_still_hints() -> None:
    prompt = _prompt(
        "compare CHEMICALS and SOLVENTS",
        [_column("category", ["CHEMICALS", "SOLVENTS"])],
    )
    assert "item.category = CHEMICALS" in prompt
    assert "item.category = SOLVENTS" in prompt


def test_chemicals_in_kl_still_hints() -> None:
    prompt = _prompt(
        "CHEMICALS in KL",
        [
            _column("category", ["CHEMICALS"]),
            _column("region", ["KL"]),
        ],
    )
    assert "item.category = CHEMICALS" in prompt
    assert "item.region = KL" in prompt


def test_sku_beta_last_month_still_hints() -> None:
    prompt = _prompt(
        "stock for SKU-BETA last month",
        [_column("sku", ["SKU-BETA"])],
    )
    assert "item.sku = SKU-BETA" in prompt
    assert "SKU-BETA" in prompt


def test_below_is_not_masked_or_hinted_from_a_shorter_value() -> None:
    prompt = _prompt(
        "below",
        [_column("category", ["low"], description="see below")],
    )
    assert "description=see below" in prompt
    assert "FILTER HINTS" not in prompt
    assert "category = low" not in prompt
    assert "DMSVAL_" not in prompt


def test_low_as_its_own_word_still_hints() -> None:
    prompt = _prompt("low", [_column("category", ["low"])])
    assert "item.category = low" in prompt


# One stored value at a time. Title-Case words are the known over-block.
_CATEGORY_VALUES = (
    "CHEMICALS",
    "chemicals",
    "SOLVENTS",
    "solvents",
    "SKU-BETA",
    "sku-beta",
    "OPEN",
    "CLOSED",
    "PENDING",
    "ALPHA",
    "BETA",
    "Q1",
    "finished goods",
    "FINISHED GOODS",
    "mixed case",
    "MIXED CASE",
    "FOOD_COLD",
    "PACKAGING",
    "hardware",
    "east",
    "widget",
    "pack",
    "active",
    "draft",
    "KL",
    "BULK",
    "RAW",
    "LOT-01",
    "Q2",
    "Q3",
    "Q4",
    "low",
    "high",
    "Electronics",
    "Hardware",
    "Finished Goods",
    "East",
    "Widget",
    "Discontinued",
    "Pack",
)


def _hint_count(values: tuple[str, ...], column: str, question_for) -> int:
    hits = 0
    for value in values:
        prompt = _prompt(question_for(value), [_column(column, [value])])
        if f"{column} = " in prompt or f"{column} ~ " in prompt:
            hits += 1
    return hits


def test_category_hints_hold_for_one_value_questions() -> None:
    hits = _hint_count(_CATEGORY_VALUES, "category", lambda value: value)
    assert len(_CATEGORY_VALUES) == 40
    assert hits == 33


def test_sku_hints_are_twenty_of_twenty() -> None:
    values = tuple(f"SKU-{number:02d}" for number in range(1, 21))
    hits = _hint_count(
        values,
        "sku",
        lambda value: f"stock for {value} last month",
    )
    assert hits == 20


def test_answer_prose_is_not_run_through_the_name_span_pass() -> None:
    prose = mask_payload(text="Warehouse Ops peaks at Warehouse A", rows=[])
    assert prose["text"] == "Warehouse Ops peaks at Warehouse A"
    plain = mask_payload(text="exact match ok", rows=[])
    assert plain["text"] == "exact match ok"


def test_show_ali_bin_on_cleared_columns_sends_no_hint() -> None:
    """Extra question tokens on a metric column must not keep the short name."""
    for column in ("category", "status", "sku", "code", "type", "flag", "id"):
        for question, value in (
            ("show ali bin", "ALI"),
            ("show ali", "ali"),
            ("show ALI BIN", "ALI"),
        ):
            prompt = _prompt(question, [_column(column, [value])])
            assert "FILTER HINTS" not in prompt, (column, question, prompt)
            assert value not in prompt
            assert value.lower() not in prompt.lower()


def test_personal_span_pass_keeps_codes_and_drops_names() -> None:
    compared = mask_personal_spans("compare CHEMICALS and SOLVENTS")
    assert "CHEMICALS" in compared
    assert "SOLVENTS" in compared
    assert "DMSMASK_name_" not in compared
    sku = mask_personal_spans("stock for SKU-BETA last month")
    assert "SKU-BETA" in sku
    assert "last month" not in sku
    assert "stock for" not in sku
    named = mask_personal_spans("how many orders did nora voss place")
    assert "nora" not in named
    assert "voss" not in named
    assert "DMSMASK_name_" in named
