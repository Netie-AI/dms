"""CCA-08 - bind a broad industry segment to the sub-segments actually landed.

Why this mechanism exists
-------------------------
The buyer's ask, paraphrased: "if I ask for the SEA countries' top sales in
agricultural, it should recognise plantation and all the different plants, and
animals should count too because that is agriculture. Then tell me which tables
and which types of business it included, and let a human go back and see every
row and column it used."

Two failures sit inside that one sentence. The first is silent under-coverage: a
question about agriculture answered from the rows spelled "Agriculture" alone,
quietly dropping "Oil Palm Plantation" and "Poultry Farming", and reporting the
remainder as the total. The second is invention: deciding a row is agricultural
because the word "crop" appears in it. "Crop Insurance Services" is a financial
product. Both produce a plausible number under a green badge, which is the one
outcome this epic exists to prevent.

So a segment term is only ever a *proposal*. ``SEGMENT_PACKS`` names the
sub-segments a segment might contain and the spellings that mean them; a granted
column's distinct landed values decide which of them exist here. Matching runs
through ``dms_executor.cca.binder`` so there is one matching rule for the whole
cascade, and it is exact on the normalised form, never substring. A landed value
that ought to count but does not match lands in ``unmatched_sample`` for a
steward to add on purpose. Widening the pack is a reviewed edit; loosening the
matcher would be a silent licence to guess.

Stage choice: an industry segment answers "what class of thing is this row",
which is the ``asset_class`` stage. The CCA-01 stage list is fixed (sense,
asset_class, geo, grain, ontology, sql, envelope), so segment rides on
asset_class rather than adding an eighth stage.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from dms_executor.cca.binder import BinderResult, TermPack, certify_pack, norm_value

#: The cascade stage a segment verdict occupies. See the module docstring.
STAGE = "asset_class"

#: Column names an industry encoding is known to live under. ``category`` is the
#: loosest of these - plenty of warehouses use it for a product taxonomy - but
#: including it costs nothing, because a column whose values match no
#: sub-segment contributes an abstain, not a wrong bind.
SEGMENT_COLUMNS: tuple[str, ...] = (
    "industry",
    "industry_segment",
    "segment",
    "sector",
    "business_type",
    "business_segment",
    "category",
    "industry_code",
    "line_of_business",
    "sic_description",
    "msic_description",
)

# The taxonomy below is a reviewable proposal about how a business might file
# its own rows, not a claim about the world. No standard body says oil palm and
# poultry belong under one heading; a steward reads this list, edits it, and
# the edit is the decision. Nothing here reaches an answer unless a granted
# column carries the spelling.
_TAXONOMY_NOTE = (
    "Reviewable proposal, not a standard. Edit the members; landed values decide."
)

AGRICULTURE = TermPack(
    name="industry_segment.agriculture",
    kind="industry segment",
    column_names=SEGMENT_COLUMNS,
    members={
        # Crops and plantation.
        "Plantation": ("plantations", "estate crops", "plantation crops", "plantation estate"),
        "Oil Palm": (
            "palm oil",
            "oil palm plantation",
            "palm oil plantation",
            "palm oil mill",
            "crude palm oil",
            "cpo",
            "fresh fruit bunch",
            "ffb",
        ),
        "Rubber": (
            "natural rubber",
            "rubber plantation",
            "rubber estate",
            "rubber tapping",
            "latex",
        ),
        "Rice and Paddy": ("paddy", "rice", "paddy farming", "rice farming", "rice milling"),
        "Sugarcane": ("sugar cane", "sugarcane plantation", "sugar cane plantation"),
        "Cocoa": ("cacao", "cocoa plantation", "cocoa farming"),
        "Coffee": ("coffee plantation", "coffee estate", "coffee farming"),
        "Tea": ("tea plantation", "tea estate", "tea farming"),
        "Coconut": ("coconut plantation", "coconut farming", "copra"),
        "Fruit Farming": (
            "fruit",
            "fruits",
            "fruit growing",
            "fruit plantation",
            "orchard",
            "fruit orchard",
            "durian",
            "banana",
            "pineapple",
        ),
        "Vegetable Farming": (
            "vegetable",
            "vegetables",
            "vegetable growing",
            "vegetable farm",
            "market gardening",
        ),
        "Horticulture": (
            "floriculture",
            "nursery",
            "plant nursery",
            "ornamental plants",
            "flower farming",
        ),
        "Grain and Cereal": (
            "grain",
            "grains",
            "cereal",
            "cereals",
            "grain farming",
            "maize",
            "corn",
        ),
        # Livestock and animal. The buyer is explicit that animals are
        # agriculture; splitting them out is how "agricultural" stops meaning
        # "crops only" by accident.
        "Livestock": ("livestock farming", "animal husbandry", "animal farming", "ranching"),
        "Poultry": (
            "poultry farming",
            "poultry farm",
            "chicken farming",
            "broiler",
            "broiler farming",
        ),
        "Cattle": ("cattle farming", "cattle ranching", "beef", "beef cattle", "feedlot"),
        "Dairy": ("dairy farming", "dairy farm", "milk production"),
        "Swine": ("pig", "pigs", "pig farming", "swine farming", "hog", "hog farming", "piggery"),
        "Goat and Sheep": ("goat", "goats", "goat farming", "sheep", "sheep farming"),
        "Egg Production": ("egg", "eggs", "egg farming", "layer farming", "table egg"),
        # Aquaculture and fisheries.
        "Aquaculture": (
            "fish farming",
            "fish farm",
            "aqua farming",
            "cage culture",
            "shrimp farming",
        ),
        "Fisheries": ("fishery", "fisheries", "fishing", "capture fisheries", "deep sea fishing"),
        "Shrimp and Prawn": ("shrimp", "prawn", "prawns", "shrimp aquaculture"),
        "Seafood": ("seafood processing", "seafood products", "marine products"),
        # Forestry.
        "Forestry": ("forest management", "forestry services", "silviculture"),
        "Logging and Timber": (
            "logging",
            "timber",
            "timber extraction",
            "sawmilling",
            "wood logging",
        ),
        "Agroforestry": ("agro forestry", "agri forestry"),
        # Agri support. A fertiliser plant is not a farm, but a buyer asking for
        # their agricultural book usually means it. It is listed separately so
        # the disclosure sentence can be argued with.
        "Agrochemicals": (
            "agrochemical",
            "agrochemicals",
            "pesticide",
            "pesticides",
            "crop protection",
        ),
        "Fertiliser": ("fertilizer", "fertilisers", "fertilizers", "compound fertilizer"),
        "Seeds": ("seed", "seed production", "seed supply", "planting material"),
        "Agri Equipment": (
            "agricultural equipment",
            "agri machinery",
            "agricultural machinery",
            "farm equipment",
            "farm machinery",
        ),
    },
    note=_TAXONOMY_NOTE,
)

# Deliberately short. A guessed member that never appears in anyone's data adds
# an absent-member line to every disclosure and buys nothing; a steward extends
# these packs against a real column.
MANUFACTURING = TermPack(
    name="industry_segment.manufacturing",
    kind="industry segment",
    column_names=SEGMENT_COLUMNS,
    members={
        "Electronics": (
            "E&E",
            "electrical and electronics",
            "electronic components",
            "semiconductor",
        ),
        "Food Manufacturing": ("food processing", "food production"),
        "Automotive": ("automotive parts", "auto parts", "vehicle assembly"),
        "Machinery and Equipment": ("machinery", "industrial equipment", "equipment manufacturing"),
        "Chemicals": ("chemical manufacturing", "petrochemical", "specialty chemicals"),
        "Plastics and Packaging": ("plastics", "plastic products", "packaging"),
    },
    note=_TAXONOMY_NOTE,
)

FOOD_AND_BEVERAGE = TermPack(
    name="industry_segment.food_and_beverage",
    kind="industry segment",
    column_names=SEGMENT_COLUMNS,
    members={
        "Restaurant": ("restaurants", "dining", "eatery", "full service restaurant"),
        "Cafe": ("cafes", "coffee shop", "coffee house", "kopitiam"),
        "Quick Service Restaurant": ("qsr", "fast food", "quick service"),
        "Catering": ("catering services", "food catering", "banquet catering"),
        "Bakery": ("bakeries", "bakery products", "confectionery"),
        "Beverage Manufacturing": ("beverage", "beverages", "soft drinks", "drinks manufacturing"),
        "Food Manufacturing": ("food processing", "packaged food"),
    },
    note=_TAXONOMY_NOTE,
)

SEGMENT_PACKS: dict[str, TermPack] = {
    "agriculture": AGRICULTURE,
    "manufacturing": MANUFACTURING,
    "food_and_beverage": FOOD_AND_BEVERAGE,
}

_PACKS_BY_NAME = {pack.name: pack for pack in SEGMENT_PACKS.values()}

# Surface form -> canonical segment. An explicit list, not a stemmer: the set of
# ways a person writes "agriculture" is small and finite, and a rule that turned
# "agricultural" into "agricultur" would also turn "agricultural bank" into a
# match. Listing the forms keeps every recognition auditable.
SEGMENT_TERMS: dict[str, str] = {
    "agriculture": "agriculture",
    "agricultural": "agriculture",
    "agri": "agriculture",
    "agribusiness": "agriculture",
    "agrifood": "agriculture",
    "agro": "agriculture",
    "farming": "agriculture",
    "farms": "agriculture",
    "plantation": "agriculture",
    "plantations": "agriculture",
    "manufacturing": "manufacturing",
    "manufacturer": "manufacturing",
    "manufacturers": "manufacturing",
    "factories": "manufacturing",
    "F&B": "food_and_beverage",
    "FNB": "food_and_beverage",
    "food and beverage": "food_and_beverage",
    "food and beverages": "food_and_beverage",
    "food service": "food_and_beverage",
}


def _phrase_index(tokens: Sequence[str], phrase: Sequence[str]) -> int | None:
    """Position of ``phrase`` in ``tokens`` as whole words, or None."""
    span = len(phrase)
    if span == 0 or span > len(tokens):
        return None
    for start in range(len(tokens) - span + 1):
        if list(tokens[start : start + span]) == list(phrase):
            return start
    return None


def propose_segment(question: str) -> str | None:
    """The broad segment a question names, or None when it names none.

    Whole-word only, so "agri" does not fire on "agriculture" twice and does not
    fire on a word that merely contains it. When two terms both appear, the one
    written first wins, and a longer phrase beats a shorter one starting at the
    same place ("food and beverage" over a bare "food").
    """
    tokens = norm_value(question).split()
    if not tokens:
        return None
    best: tuple[int, int, str] | None = None
    for surface, canonical in SEGMENT_TERMS.items():
        phrase = norm_value(surface).split()
        at = _phrase_index(tokens, phrase)
        if at is None:
            continue
        rank = (at, -len(phrase))
        if best is None or rank < (best[0], best[1]):
            best = (at, -len(phrase), canonical)
    return None if best is None else best[2]


def _no_segment(question: str, constraint_id: str) -> BinderResult:
    """ABSTAIN because the ask never named a segment. Not a data gap."""
    return BinderResult(
        stage=STAGE,
        constraint_id=constraint_id,
        candidate=question.strip(),
        pack="(none)",
        status="ABSTAIN",
        reasons=(
            "the question names no industry segment, so there is nothing to bind; "
            f"known segments: {', '.join(SEGMENT_PACKS)}",
        ),
    )


def bind_segment(
    question: str,
    *,
    warehouse: Path | str | None,
    tables: Iterable[str],
    constraint_id: str = "segment-1",
) -> BinderResult:
    """CERTIFY a segment against the sub-segments landed in a granted column.

    ABSTAIN, naming the gap, when the ask names no segment, when no granted
    column carries a segment encoding, or when a segment column exists but
    carries no value matching any sub-segment. That last case is the one worth
    the code: a filter over an unmatched segment executes cleanly and returns
    zero rows, and zero rows summed is a number a reader will believe.
    """
    key = propose_segment(question)
    if key is None:
        return _no_segment(question, constraint_id)
    return certify_pack(
        stage=STAGE,
        constraint_id=constraint_id,
        candidate=key.replace("_", " "),
        pack=SEGMENT_PACKS[key],
        warehouse=warehouse,
        tables=tables,
    )


def included_business_types(result: BinderResult) -> list[str]:
    """The canonical sub-segments that actually matched, in pack order.

    Pack order, not scan order: the list is read by a human comparing two
    answers, and a list that reshuffles because DuckDB returned distinct values
    in another order looks like the answer changed when it did not.
    """
    pack = _PACKS_BY_NAME.get(result.pack)
    if pack is None:
        return sorted(result.matched)
    return [member for member in pack.members if member in result.matched]


def disclosure(result: BinderResult) -> str:
    """The sentence the buyer reads: what was included, from where, and what was not.

    ``BinderResult.coverage_note`` already states members included and members
    the data does not carry; this adds the tables read and the landed values
    that were left out, so the reader can go back to the rows.
    """
    note = result.coverage_note()
    if not result.certified:
        return note
    parts = [f"{note}. Read from {', '.join(result.tables)}"]
    if result.unmatched_sample:
        parts.append(
            "Values in that column counted as outside the segment: "
            f"{', '.join(result.unmatched_sample)}"
        )
    return ". ".join(parts) + "."


__all__ = [
    "AGRICULTURE",
    "FOOD_AND_BEVERAGE",
    "MANUFACTURING",
    "SEGMENT_COLUMNS",
    "SEGMENT_PACKS",
    "SEGMENT_TERMS",
    "STAGE",
    "bind_segment",
    "disclosure",
    "included_business_types",
    "propose_segment",
]
