"""VALUE-HINT-SPAN-01: value hints are looked up on the masker's output.

A partial or full person name in the question must not seed a hint on a
mask-cleared column. Synthetic names only. No word list in the product.
"""

from __future__ import annotations

from typing import Any

from cortex_client.compute import _insights_body
from dms_core.pii import NAME_MASK_KEY, fail_closed_mask_payload, mask_payload
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


def _built(question: str, datasets: list[dict[str, Any]]):
    return build_schema_context(
        question, {"dialect": "mysql", "datasets": datasets}
    )


def _body(question: str, datasets: list[dict[str, Any]]) -> dict[str, Any]:
    built = _built(question, datasets)
    return _insights_body(
        question,
        session_id="sess-span",
        space_id="space-span",
        ontology={
            "schema_context": built.prompt,
            NAME_MASK_KEY: built.name_mask,
        },
    )


def test_mask_cleared_column_does_not_hint_a_person_span() -> None:
    """A stored name is masked, so none of its parts can hint."""
    for question, parts in _NAME_QUESTIONS:
        full = parts[0]
        datasets = [
            {
                "name": "person",
                "columns": [_column("full_name", [full])],
            },
            {
                "name": "item",
                "columns": [_column("category", list(parts))],
            },
        ]
        prompt = _built(question, datasets).prompt
        emitted = _emitted_hints(prompt)
        for part in parts:
            assert part not in emitted, (question, part, emitted)
            assert all(part.casefold() not in hint.casefold() for hint in emitted), (
                question,
                part,
                emitted,
            )


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
    assert hits == 34


def test_category_hints_hold_inside_a_sentence() -> None:
    hits = _hint_count(
        _CATEGORY_VALUES, "category", lambda value: f"list {value}"
    )
    assert hits == 34


def test_sku_hints_are_twenty_of_twenty() -> None:
    values = tuple(f"SKU-{number:02d}" for number in range(1, 18)) + (
        "resin",
        "pigment",
        "wax",
    )
    hits = _hint_count(
        values,
        "sku",
        lambda value: f"stock for {value} last month",
    )
    assert hits == 20


_OVERMASK = (
    ("how many shipments are delayed", "status", "delayed"),
    ("list spare parts", "category", "spare parts"),
    ("stock in KUALA LUMPUR", "region", "KUALA LUMPUR"),
    ("total quantity of inbound transactions", "type", "inbound"),
    ("list raw material", "category", "raw material"),
    ("list export", "category", "export"),
    ("list import", "category", "import"),
    ("list urgent", "category", "urgent"),
    ("stock in JOHOR BAHRU", "region", "JOHOR BAHRU"),
    ("stock in shah alam", "region", "shah alam"),
    ("list IBC TOTE", "category", "IBC TOTE"),
    ("list HOT ROLLED COIL", "category", "HOT ROLLED COIL"),
    ("list cold room", "category", "cold room"),
    ("list in transit", "status", "in transit"),
    ("list on hold", "status", "on hold"),
)


def test_in_sentence_values_still_hint() -> None:
    for question, column, value in _OVERMASK:
        prompt = _prompt(question, [_column(column, [value])])
        assert f"{column} = {value}" in prompt, (question, prompt)


def test_answer_prose_is_not_run_through_the_name_span_pass() -> None:
    prose = mask_payload(text="Warehouse Ops peaks at Warehouse A", rows=[])
    assert prose["text"] == "Warehouse Ops peaks at Warehouse A"
    plain = mask_payload(text="exact match ok", rows=[])
    assert plain["text"] == "exact match ok"


def test_show_ali_bin_on_cleared_columns_still_hints() -> None:
    """A cleared non-person cell is not a name fragment."""
    for column in ("category", "status", "sku", "code", "type", "flag", "id"):
        prompt = _prompt("show ali bin", [_column(column, ["ALI"])])
        assert f"{column} = ALI" in prompt or f"{column} = ali" in prompt


def test_stored_name_is_masked_in_the_cortex_body() -> None:
    datasets = [
        {"name": "person", "columns": [_column("full_name", ["nora voss"])]},
        {"name": "item", "columns": [_column("category", ["voss"])]},
    ]
    for question in (
        "what did nora voss buy",
        "what did NORA VOSS buy",
        "what did Nora Voss buy",
    ):
        body = _body(question, datasets)
        assert "voss" not in body["question"].casefold()
        assert "voss" not in body["intent"].casefold()
        assert "DMSMASK_name_" in body["question"]
        assert "FILTER HINTS" not in str(body.get("schema_context") or "")


def test_unstored_name_reaches_cortex_as_typed() -> None:
    datasets = [
        {"name": "item", "columns": [_column("category", ["CHEMICALS"])]},
    ]
    question = "what did nora voss buy"
    body = _body(question, datasets)
    assert body["question"] == question
    assert body["intent"] == question
    assert "FILTER HINTS" not in str(body.get("schema_context") or "")


def test_how_many_open_still_hints_and_a_cleared_token_still_hints() -> None:
    """A cleared value hints even when the question has words around it."""
    metric = _prompt("how many OPEN", [_column("category", ["OPEN"])])
    assert "category = OPEN" in metric
    east = _prompt("how many east", [_column("category", ["east"])])
    assert "category = east" in east
    parts = _prompt(
        "list spare parts",
        [_column("category", ["PARTS"])],
        table="inventory",
    )
    assert "inventory.category = parts" in parts


def test_source_mask_keeps_metric_text_and_drops_a_stored_name() -> None:
    kept = fail_closed_mask_payload(
        text="compare CHEMICALS and SOLVENTS",
        name_values=["nora voss"],
        exempt_values=["CHEMICALS", "SOLVENTS"],
        schema_terms=["item", "category"],
    )["text"]
    assert kept == "compare CHEMICALS and SOLVENTS"
    asked = fail_closed_mask_payload(
        text="stock for SKU-BETA last month",
        name_values=["nora voss"],
        exempt_values=["SKU-BETA"],
        schema_terms=["item"],
    )["text"]
    assert asked == "stock for SKU-BETA last month"
    named = fail_closed_mask_payload(
        text="how many orders did nora voss place",
        name_values=["nora voss"],
        exempt_values=[],
        schema_terms=["item"],
    )["text"]
    assert "nora" not in named.casefold()
    assert "DMSMASK_name_" in named
    titled = fail_closed_mask_payload(
        text="Finished Goods last month",
        name_values=["Nora Voss"],
        exempt_values=["Finished Goods"],
        schema_terms=["Item Category"],
    )["text"]
    assert titled == "Finished Goods last month"
    label = fail_closed_mask_payload(
        text="Open the Item Category",
        name_values=[],
        exempt_values=[],
        schema_terms=["Item Category"],
    )["text"]
    assert label == "Open the Item Category"


def _partial_names() -> list[tuple[str, str, str]]:
    """60 questions: stored and unstored, long, mixed-case, multi-part."""
    names = (
        ("nora voss", "voss"),
        ("NORA VOSS", "VOSS"),
        ("Nora Voss", "Voss"),
        ("nora VOSS", "VOSS"),
        ("muhammad hafiz", "hafiz"),
        ("MUHAMMAD HAFIZ", "HAFIZ"),
        ("priyadarshini rajan", "rajan"),
        ("PRIYADARSHINI RAJAN", "RAJAN"),
        ("RAJESWARI MUNIANDY", "MUNIANDY"),
        ("ali bin abu", "abu"),
        ("ALI BIN ABU", "ABU"),
        ("mina cole", "cole"),
    )
    frames = (
        "lookup {name} in the ledger",
        "sales by {name} today",
        "meet {name} after the count",
        "call {name} back",
        "page {name} now",
    )
    out: list[tuple[str, str, str]] = []
    for name, fragment in names:
        for frame in frames:
            if len(out) >= 60:
                return out
            question = frame.format(name=name)
            stored = name if len(out) % 2 == 0 else ""
            out.append((question, stored, fragment))
    return out


def test_partial_names_hint_nothing() -> None:
    """A person-classified column is masked, so its parts do not hint.

    The same fragment stored only in a cleared column may hint. That column
    was classified as non-person.
    """
    rows = _partial_names()
    assert len(rows) == 60
    stored_leaks = 0
    for question, stored, fragment in rows:
        datasets = [
            {"name": "item", "columns": [_column("category", [fragment])]},
        ]
        if stored:
            datasets.insert(
                0, {"name": "person", "columns": [_column("full_name", [stored])]}
            )
        built = _built(question, datasets)
        emitted = _emitted_hints(built.prompt)
        if stored:
            if emitted:
                stored_leaks += 1
            assert not emitted, (question, stored, fragment, emitted)
            body = _body(question, datasets)
            assert fragment.casefold() not in body["question"].casefold(), question
    assert stored_leaks == 0


def test_overmask_examples_stay_typed_and_spare_parts_hints() -> None:
    """Red on a521d0d: these questions were masked, or PARTS did not hint."""
    cases = (
        (
            "top 5 SKUs by sales",
            [{"name": "meta", "columns": [_column("value", ["5"])]}],
            "5",
        ),
        (
            "high severity alerts",
            [{"name": "alerts", "columns": [_column("severity", ["high"])]}],
            "high",
        ),
        (
            "stock at Warehouse A",
            [{"name": "locations", "columns": [_column("name", ["Warehouse A"])]}],
            "Warehouse A",
        ),
    )
    for question, datasets, raw in cases:
        body = _body(question, datasets)
        assert body["question"] == question, body["question"]
        assert body["intent"] == question
        assert raw in body["question"]
        assert "DMSMASK_" not in body["question"]
        assert "FILTER HINTS" not in str(body.get("schema_context") or "")


def test_cleared_place_hints_and_unsure_place_does_not() -> None:
    for value, question in (
        ("Warehouse A", "stock at Warehouse A"),
        ("Kuala Lumpur", "stock in Kuala Lumpur"),
    ):
        cleared_prompt = _prompt(question, [_column("region", [value])])
        assert f"region = {value}" in cleared_prompt
        cleared = _body(
            question,
            [{"name": "item", "columns": [_column("region", [value])]}],
        )
        assert cleared["question"] == question
        assert "DMSMASK_" not in cleared["question"]
        unsure_prompt = _prompt(
            question, [_column("name", [value])], table="locations"
        )
        assert "FILTER HINTS" not in unsure_prompt
        unsure = _body(
            question,
            [{"name": "locations", "columns": [_column("name", [value])]}],
        )
        assert unsure["question"] == question
        assert "DMSMASK_" not in unsure["question"]


def test_unsure_person_rep_stays_typed_and_sends_no_hint() -> None:
    question = "what did nora voss buy"
    body = _body(
        question,
        [{"name": "person", "columns": [_column("rep", ["nora voss"])]}],
    )
    assert body["question"] == question
    assert "FILTER HINTS" not in str(body.get("schema_context") or "")
