"""Link a question to ontology measures by rank, not by a phrase table.

``DMS_NO_WORD_RULES`` defaults to 0. On, the ask's own measures are ranked
with the verified-example embedder, or Okapi BM25 when that embedder is
absent. Same floor and width as GROUND-DEFS: score above 0.5, top 3.
The text ranked is the measure name, its description, and synonyms stored
on the measure (a steward edits those; the demo copy lives in
``ontology_spine.yaml``). A unique top score links that measure. A tie,
or nothing above the floor, reconfirms and is not served.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dms_executor.ontology import Measure, Ontology

FLAG = "DMS_NO_WORD_RULES"
# ponytail: same floor and k as ground_defs. A live A/B that shows a real
# hit at or under 0.5 should split the floors. rank_texts keeps score > floor.
_K = 3
_SCORE_FLOOR = 0.5
REASON_UNRANKED = "measure_unranked"
REASON_TIE = "measure_tie"
REASON_MISMATCH = "measure_link_mismatch"
LINK_REASONS = frozenset({REASON_UNRANKED, REASON_TIE, REASON_MISMATCH})
_SPINE = Path(__file__).with_name("ontology_spine.yaml")


def no_word_rules_enabled() -> bool:
    """True only when ``DMS_NO_WORD_RULES`` is the digit 1. Unset is off."""
    return os.environ.get(FLAG, "0").strip() == "1"


@dataclass(frozen=True)
class MeasureLink:
    name: str | None
    reason_code: str | None
    ranked: tuple[tuple[str, float], ...]


def spine_synonyms(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Synonym phrases from the spine YAML. Empty when the file is absent."""
    target = path or _SPINE
    if not target.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    data = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("measure_synonyms") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, val in raw.items():
        name = str(key or "").strip()
        phrases = _phrases(val)
        if name and phrases:
            out[name] = phrases
    return out


def attach_spine_synonyms(onto: Ontology, path: Path | None = None) -> None:
    """Copy spine phrases onto demo measures that do not already have some."""
    found = spine_synonyms(path)
    if not found:
        return
    for name, phrases in found.items():
        measure = onto.measures.get(name)
        if measure is None or measure.synonyms:
            continue
        onto.measures[name] = replace(measure, synonyms=phrases)


def rank_measure_link(
    question: str,
    ontology: Ontology | None,
    *,
    embedder: Callable[[list[str]], Any] | None = None,
    context: dict[str, Any] | None = None,
) -> MeasureLink:
    """Unique measure above the floor, or a typed reason and no name.

    ``ontology`` wins when it has measures. Otherwise the retrieve slice
    in ``context`` is ranked. No phrase table.
    """
    from dms_executor.verified_queries import rank_texts

    measures = _candidates(ontology, context)
    if not measures:
        return MeasureLink(None, None, ())
    if not (question or "").strip():
        return MeasureLink(None, REASON_UNRANKED, ())
    docs = [_doc(item) for item in measures]
    ranked = rank_texts(
        question,
        docs,
        embedder,
        limit=_K,
        floor=_SCORE_FLOOR,
    )
    picked = tuple((measures[index].name, score) for score, index in ranked)
    if not picked:
        return MeasureLink(None, REASON_UNRANKED, ())
    if len(ranked) >= 2 and ranked[0][0] == ranked[1][0]:
        return MeasureLink(None, REASON_TIE, picked)
    return MeasureLink(picked[0][0], None, picked)


def reconfirm_for_reason(
    reason: str | None,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    """Reconfirm envelope for a link reason. None when the flag is off.

    Yes re-asks the same question. No SQL is parked.
    """
    code = str(reason or "").strip()
    if not no_word_rules_enabled() or code not in LINK_REASONS:
        return None
    from dms_executor.sql_vote import reconfirm_envelope

    env = reconfirm_envelope(
        question=question,
        why=code,
        closest_question=question,
        space_id=space_id,
        session_id=session_id,
    )
    block = env.get("reconfirm")
    if isinstance(block, dict):
        block["code"] = code
        block["reason_code"] = code
    return env


def _candidates(
    ontology: Ontology | None,
    context: dict[str, Any] | None,
) -> list[Measure]:
    if ontology is not None and ontology.measures:
        return [item for item in ontology.measures.values() if item.name]
    raw = context.get("measures") if isinstance(context, dict) else None
    if not isinstance(raw, dict):
        return []
    out: list[Measure] = []
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        label = str(name or "").strip()
        if not label:
            continue
        out.append(
            Measure(
                label,
                str(spec.get("grain") or ""),
                "",
                description=str(spec.get("description") or ""),
                synonyms=_phrases(spec.get("synonyms")),
            )
        )
    return out


def _doc(measure: Measure) -> str:
    parts: list[str] = [measure.name.replace("_", " "), measure.description or ""]
    parts.extend(measure.synonyms)
    return "\n".join(part for part in parts if part)


def _phrases(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        items: Sequence[Any] = [raw]
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        return ()
    out: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split())
        if text and text not in out:
            out.append(text)
    return tuple(out)
