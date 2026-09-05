"""Who decides that a question carries a filter: a word list, or a model.

The measured problem
--------------------
``cascade._proposals`` is a frozenset of terms and a two-token cue window. On
1284 independently labelled questions it engaged wrongly once in 1237, which is
excellent, and recognised zero of the filters the packs actually claim, which is
the whole job. A recogniser that never fires is quiet, not correct.

The epic charter said so from the start. ``cca/__init__.py`` opens with "An LLM
may *propose* that SEA means eleven countries" - the word list was the
placeholder for a model, and it is the half the measurement condemns.

The split this module preserves
------------------------------
**The model proposes. DMS certifies. Neither does the other's job.**

  proposes    which of the four kinds this question constrains, and the
              verbatim span that names each one. That is reading comprehension,
              which is what a model is for and what a cue window is not.

  certifies   whether the pack claims that span, and whether a granted column
              actually carries it. That is ``binder.certify_pack``, unchanged,
              measured at exact-match precision, and it is the reason a filter
              cannot be invented.

So a model saying "geo: Malaysia" does not put Malaysia into a filter. It puts
Malaysia to the pack; the pack either claims it or does not; and a claimed
member still has to appear in a granted column before any row is excluded. A
model that hallucinates a country produces an abstention naming the country,
never a WHERE clause. That property is what makes it safe to put a model on
this path at all, and it is asserted in tests/test_cca_proposer.py.

Failure is loud
---------------
When the model call fails, times out or returns something unparseable, this
does NOT quietly fall back to the word list and carry on. Silent degradation
wearing a working system's clothes is the failure class this repo has a rule
about. The proposal comes back ``degraded`` with the reason attached, the
cascade does not engage, and the reason reaches the trace.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from dms_executor.cca.binder import BinderResult, TermPack, certify_pack

#: Which recogniser runs. ``lexicon`` is the shipped default and the only one
#: that needs no credential. Nothing here changes what the cascade does with a
#: proposal, and nothing here turns the cascade on: DMS_CCA_CASCADE still gates
#: that, and still defaults to 0.
PROPOSER_ENV = "DMS_CCA_PROPOSER"
MODEL_ENV = "DMS_CCA_PROPOSER_MODEL"

#: Default model. Not chosen for cost: this call decides whether a customer's
#: filter is noticed at all, and the cheaper tiers are the ones most likely to
#: over-trigger, which is the failure that currently costs nothing (0.08 pct).
#: scripts/cca_proposer_bench.py measures the alternatives against the labelled
#: corpus so the choice can be a number.
DEFAULT_MODEL = "claude-opus-5"

KINDS = ("sense", "asset_class", "geo", "segment")

#: cascade._proposals speaks this dialect; asset_class and segment share a stage.
_PROPOSAL_KEY = {"sense": "sense", "asset_class": "class", "geo": "geo", "segment": "segment"}


@dataclass(frozen=True)
class Proposal:
    """What a recogniser thinks the question constrains. A claim, not a filter."""

    kinds: tuple[str, ...] = ()
    #: kind -> the verbatim words in the question that name it. The span is an
    #: extraction; deciding whether the pack claims it is not the proposer's job.
    spans: Mapping[str, str] = field(default_factory=dict)
    polarity: str = "none"
    why: str = ""
    #: True when the recogniser could not run. Never silently treated as "no
    #: filter here": the cascade records it and stays out of the ask.
    degraded: bool = False
    degraded_reason: str = ""
    source: str = "lexicon"

    def as_proposal_flags(self) -> dict[str, bool]:
        """The dict shape ``cascade._proposals`` returns."""
        return {_PROPOSAL_KEY[k]: (k in self.kinds) for k in KINDS}

    @property
    def engages(self) -> bool:
        return bool(self.kinds) and not self.degraded


@runtime_checkable
class AskProposer(Protocol):
    name: str

    def propose(self, question: str) -> Proposal: ...


class ProposerUnavailable(RuntimeError):
    """The configured recogniser cannot run. Raised at wiring, never swallowed."""


# ---------------------------------------------------------------------------
# The instruction. Describes the four kinds in the product's own words and
# names no pack member, so the model is an independent reader rather than the
# word list with extra steps. The worked examples deliberately use countries
# the geo pack does not claim (Brazil, Poland): a member in the prompt is a
# leak however innocent the intent, and a test asserts the absence.
# ---------------------------------------------------------------------------

SYSTEM = """\
You read one business question and report which row-restricting constraints it \
carries. You do not answer the question and you do not write SQL.

Report a constraint only when it NARROWS WHICH ROWS COUNT. Four kinds:

  sense        the nature of the deal: leasing or renting versus buying or \
selling, or residential renting specifically. Not the word "sales" used as a \
number to total.
  asset_class  the class of property: commercial, residential, or mixed use. \
Not a product category, and not a building used as a location.
  geo          a named country, or a named region spanning several countries.
  segment      an industry sector or line of business. Not a product category, \
not a SKU, not a department.

THE TEST THAT DECIDES MOST CASES. Restricting is not grouping and is not \
selecting.
  "total sales in Brazil"     restricts to Brazil            -> geo
  "total sales by country"    groups by country              -> nothing
  "which country sold most"   asks for a country as answer   -> nothing
  "revenue for our Polish entity"                            -> geo

NOT any of the four kinds, however much they look like filters: a city, state, \
province, postcode, port, warehouse, site, plant or branch; a date or range; a \
currency; a threshold or top-N; a product code, SKU, brand, model or category; \
a customer, supplier or employee name; a department; a status such as delayed \
or paid; a file or sheet name.

For every kind you report, give the VERBATIM span from the question that names \
it. Copy the words exactly as written. The span is checked against a declared \
list downstream, so an approximation or a normalisation is worse than useless.

Polarity: include (restricts to it), exclude (restricts away from it), \
comparison (two set head to head), mixed (more than one), none.

Report nothing rather than guess. A question that carries no constraint of \
these four kinds is the common case and reporting it as empty is the correct \
answer, not a failure."""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "span": {"type": "string"},
                },
                "required": ["kind", "span"],
                "additionalProperties": False,
            },
        },
        "polarity": {
            "type": "string",
            "enum": ["include", "exclude", "comparison", "mixed", "none"],
        },
        "why": {"type": "string"},
    },
    "required": ["constraints", "polarity", "why"],
    "additionalProperties": False,
}


def _proposal_from_payload(payload: Mapping[str, Any], *, source: str) -> Proposal:
    kinds: list[str] = []
    spans: dict[str, str] = {}
    for item in payload.get("constraints") or ():
        kind = str(item.get("kind") or "")
        span = str(item.get("span") or "").strip()
        if kind in KINDS and span and kind not in kinds:
            kinds.append(kind)
            spans[kind] = span
    polarity = str(payload.get("polarity") or "none")
    if polarity not in {"include", "exclude", "comparison", "mixed", "none"}:
        polarity = "none"
    if not kinds:
        polarity = "none"
    return Proposal(
        kinds=tuple(kinds),
        spans=spans,
        polarity=polarity,
        why=str(payload.get("why") or "")[:400],
        source=source,
    )


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class LexiconProposer:
    """The shipped word list. Quiet, cheap, and recognises almost nothing."""

    name = "lexicon"

    def propose(self, question: str) -> Proposal:
        from dms_executor.cca.asset_class import parse_class_intent
        from dms_executor.cca.geo import propose_countries, propose_region
        from dms_executor.cca.segment import propose_segment
        from dms_executor.cca.sense import propose_senses

        kinds: list[str] = []
        spans: dict[str, str] = {}
        senses = propose_senses(question)
        if senses:
            kinds.append("sense")
            spans["sense"] = " + ".join(senses)
        include, exclude = parse_class_intent(question)
        if include or exclude:
            kinds.append("asset_class")
            spans["asset_class"] = " + ".join([*include, *exclude])
        seg = propose_segment(question)
        if seg:
            kinds.append("segment")
            spans["segment"] = seg
        region = propose_region(question)
        countries = propose_countries(question)
        if region or countries:
            kinds.append("geo")
            spans["geo"] = region or " + ".join(countries)
        return Proposal(
            kinds=tuple(kinds),
            spans=spans,
            polarity="include" if kinds else "none",
            why="declared term list plus a two-token cue window",
            source="lexicon",
        )


class AnthropicProposer:
    """Reads the question with a Claude model, still proposes nothing binding.

    The system prompt is stable and cached, so the marginal cost of a proposal
    is one short user turn plus a short structured reply.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens: int = 1024,
        effort: str = "low",
        timeout: float = 20.0,
        client: Any = None,
    ) -> None:
        if client is not None:
            # Injected for tests. The request shape is the part that cannot be
            # checked any other way without a credential, and a typo in it would
            # otherwise only surface in production.
            self._client = client
        else:
            try:
                import anthropic
            except ModuleNotFoundError as exc:  # pragma: no cover - import guard
                raise ProposerUnavailable(
                    "the anthropic SDK is not installed; pip install 'anthropic' or set "
                    f"{PROPOSER_ENV}=lexicon"
                ) from exc
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ProposerUnavailable(
                    "ANTHROPIC_API_KEY is not set. The proposer will not run without a "
                    "credential, and it will not quietly become the word list instead."
                )
            self._client = anthropic.Anthropic(api_key=key, timeout=timeout)
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._effort = effort
        #: Set by the last call. The bench reads it to price a run.
        self.last_usage: dict[str, int] = {}

    def request_params(self, question: str) -> dict[str, Any]:
        """The exact request. Split out so a test can assert it without a key.

        Built as a plain dict because the SDK's overloads do not accept dict
        literals under mypy's inference; the shape is pinned by
        tests/test_cca_proposer.py instead, which is the check that would
        actually catch a typo here.
        """
        return {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": SYSTEM,
                    # Identical on every ask, so it is the cache prefix and the
                    # marginal cost of a proposal is one short question.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": question}],
            "output_config": {
                # Classification, not reasoning: low effort is the right tier
                # and keeps this off the ask path's latency budget.
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
        }

    def propose(self, question: str) -> Proposal:
        try:
            response = self._client.messages.create(**self.request_params(question))
        except Exception as exc:  # noqa: BLE001 - every failure is degraded, not empty
            return Proposal(
                degraded=True,
                degraded_reason=f"{type(exc).__name__}: {exc}"[:300],
                source=f"anthropic:{self.model}",
            )
        usage = getattr(response, "usage", None)
        self.last_usage = {
            "input": getattr(usage, "input_tokens", 0) or 0,
            "output": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        }
        if getattr(response, "stop_reason", None) == "refusal":
            return Proposal(
                degraded=True,
                degraded_reason="model declined to classify this question",
                source=f"anthropic:{self.model}",
            )
        try:
            text = next(b.text for b in response.content if b.type == "text")
            payload = json.loads(text)
        except (StopIteration, AttributeError, json.JSONDecodeError) as exc:
            return Proposal(
                degraded=True,
                degraded_reason=f"unparseable proposal: {type(exc).__name__}",
                source=f"anthropic:{self.model}",
            )
        return _proposal_from_payload(payload, source=f"anthropic:{self.model}")


class CortexProposer:
    """One engine, one key. Inert until Cortex serves a classify route.

    Deliberately raises rather than returning empty proposals. An adapter that
    quietly proposes nothing would read, from every dashboard, exactly like a
    model that found no filters.

    The contract this needs on the Cortex side, so the ticket can be written
    without a second round trip:

        POST /v1/contract/classify
        request   {"question": str, "kinds": ["sense","asset_class","geo","segment"]}
        response  {"constraints": [{"kind": str, "span": str}],
                   "polarity": "include|exclude|comparison|mixed|none",
                   "why": str}

    The span must be a verbatim substring of the question. Membership and
    landed-value certification stay on the DMS side and must not move: the
    engine proposing AND certifying is the single-source failure this split
    exists to prevent.
    """

    name = "cortex"
    ENDPOINT = "/v1/contract/classify"

    def __init__(self, cortex: Any = None) -> None:
        self._cortex = cortex
        if cortex is None or not hasattr(cortex, "classify"):
            raise ProposerUnavailable(
                f"Cortex does not serve {self.ENDPOINT} yet. The vendored contract "
                "(contract/openapi-1.2.0.json) declares ask, submit, drillthrough, "
                "tools and the two ledger routes, and no classify route. Land it "
                f"Cortex-side, bump cortex-contract, then set {PROPOSER_ENV}=cortex."
            )

    def propose(self, question: str) -> Proposal:  # pragma: no cover - no endpoint yet
        try:
            payload = self._cortex.classify({"question": question, "kinds": list(KINDS)})
        except Exception as exc:  # noqa: BLE001
            return Proposal(
                degraded=True,
                degraded_reason=f"{type(exc).__name__}: {exc}"[:300],
                source="cortex",
            )
        return _proposal_from_payload(payload, source="cortex")


def get_proposer(*, cortex: Any = None) -> AskProposer:
    """The configured recogniser. Unknown value is an error, not a default."""
    choice = (os.environ.get(PROPOSER_ENV) or "lexicon").strip().lower()
    if choice == "lexicon":
        return LexiconProposer()
    if choice == "anthropic":
        return AnthropicProposer()
    if choice == "cortex":
        return CortexProposer(cortex=cortex)
    raise ProposerUnavailable(
        f"{PROPOSER_ENV}={choice!r} is not a recogniser. Use lexicon, anthropic or cortex."
    )


# ---------------------------------------------------------------------------
# Certifying a proposed span. This is where the model's claim meets the pack.
# ---------------------------------------------------------------------------


def _pack_for(kind: str) -> tuple[TermPack | None, dict[str, TermPack]]:
    if kind == "geo":
        from dms_executor.cca.geo import GEO_REGION_MEMBERS

        return None, dict(GEO_REGION_MEMBERS)
    if kind == "asset_class":
        from dms_executor.cca.asset_class import ASSET_CLASS_PACK

        return ASSET_CLASS_PACK, {}
    if kind == "segment":
        from dms_executor.cca.segment import SEGMENT_PACKS

        return None, dict(SEGMENT_PACKS)
    from dms_executor.cca.sense import TENURE

    return TENURE, {}


def certify_span(
    kind: str,
    span: str,
    *,
    warehouse: Any,
    tables: Sequence[str],
    constraint_id: str | None = None,
) -> BinderResult:
    """Certify a proposed span, or abstain saying which authority refused it.

    Two refusals, and telling them apart is the point:

    ``the pack does not claim this``  the model read the question correctly and
        named something the shipped packs never covered. Aruba is a country; the
        geo pack is eleven Southeast Asian states. That is a coverage gap, and
        adding the member is a deliberate act by a person, not a side effect of
        a model mentioning it.

    ``no granted column carries it``  the pack claims the member and this
        Space's data does not have it. The existing binder wording.

    Neither path lets the span itself become a filter value. Only a landed value
    does that, and only ``certify_pack`` produces one.
    """
    cid = constraint_id or f"{kind}-llm"
    stage = "asset_class" if kind == "segment" else kind
    single, many = _pack_for(kind)
    candidates = [single] if single is not None else list(many.values())

    from dms_executor.cca.binder import norm_value

    key = norm_value(span)
    for pack in candidates:
        if pack is None:
            continue
        index = pack.alias_index()
        member = index.get(key)
        if member is None:
            # Try the span as a whole-phrase member name of a broad pack (a
            # segment pack is keyed by its own name, e.g. "agriculture").
            if key and key == norm_value(pack.name.split(".")[-1]):
                member = None
            else:
                continue
        narrowed = TermPack(
            name=f"{pack.name}[{member}]" if member else pack.name,
            kind=pack.kind,
            column_names=pack.column_names,
            members=({member: pack.members[member]} if member else pack.members),
            note=pack.note,
        )
        return certify_pack(
            stage=stage,
            constraint_id=cid,
            candidate=span,
            pack=narrowed,
            warehouse=warehouse,
            tables=tables,
        )

    # Also allow a broad segment name ("agriculture") to select a whole pack.
    for name, pack in many.items():
        if key == norm_value(name):
            return certify_pack(
                stage=stage,
                constraint_id=cid,
                candidate=span,
                pack=pack,
                warehouse=warehouse,
                tables=tables,
            )

    return BinderResult(
        stage=stage,
        constraint_id=cid,
        candidate=span,
        pack="(no pack claims this)",
        status="ABSTAIN",
        tables=tuple(str(t) for t in tables),
        reasons=(
            f"the question names {span!r} as a {kind} constraint and no shipped "
            f"{kind} pack claims it. The proposal is not evidence that this value "
            "exists; adding it to a pack is a deliberate act by a person.",
        ),
    )


__all__ = [
    "DEFAULT_MODEL",
    "KINDS",
    "MODEL_ENV",
    "PROPOSER_ENV",
    "SYSTEM",
    "AnthropicProposer",
    "AskProposer",
    "CortexProposer",
    "LexiconProposer",
    "Proposal",
    "ProposerUnavailable",
    "certify_span",
    "get_proposer",
]
