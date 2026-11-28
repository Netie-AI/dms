"""Space catalog on the session manifest (FDE-CORTEX-SPACE-CATALOG-01).

``DMS_SPACE_CATALOG`` defaults off. Off, nothing here is attached and the
signed manifest bytes stay the ones ``canonical_manifest_bytes`` already
produces.

On, a non-demo Space's bind carries ``space_catalog`` on the manifest JSON.
The key is not a signed field: it is stamped beside the manifest and left
out of ``model_dump``, so the signature covers the same bytes as today.
Cortex reads that object when its own flag is on and builds chooser context
only from it. The demo Space sends no object, so the demo catalog stays the
demo Space's.

Agreed shape (objects, links, measures, definitions, granted tables):

    space_catalog = {
        "objects": [{"name", "relation", "key"}],
        "links": [{
            "name", "from_object", "from_columns",
            "to_object", "to_columns", "cardinality",
        }],
        "measures": [{"name", "grain", "expression", "description"}],
        "definitions": [{"name", "grain", "text"}],
        "granted_tables": [str],
    }

``definitions[].text`` is the measure expression. An empty ``measures`` list
means the Space has no approved measure. Cortex then returns
``space_catalog_handoff = "generative"`` instead of a chooser reconfirm, and
DMS continues the generative path. A miss is a typed reconfirm that names
only this Space's tables and measures.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from dms_executor.demo_grants import is_demo_space
from dms_executor.ontology import Ontology

_LOG = logging.getLogger(__name__)

SPACE_CATALOG_ENV = "DMS_SPACE_CATALOG"
SPACE_CATALOG_FIELD = "space_catalog"
HANDOFF_FIELD = "space_catalog_handoff"
HANDOFF_GENERATIVE = "generative"
#: Private attr on the contract Manifest. Not a model field, so it is absent
#: from ``model_dump`` and from ``canonical_manifest_bytes``.
_ATTR = "_dms_space_catalog"

_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


def space_catalog_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when ``DMS_SPACE_CATALOG`` is exactly ``1``."""
    source = os.environ if env is None else env
    return str(source.get(SPACE_CATALOG_ENV, "")).strip() == "1"


def _fold(name: object) -> str:
    return str(name or "").strip().translate(_ASCII_LOWER)


def load_space_catalog_ontology(space_id: str | None) -> Ontology | None:
    """The Space's stored ontology, or None when it has none or cannot be read.

    A read failure is logged by class and becomes an empty catalog. The ask
    still sends the granted tables, and it does not fall back to the demo
    ontology.
    """
    if not space_id or is_demo_space(space_id):
        return None
    try:
        from dms_executor.space_ontology import load_space_ontology

        return load_space_ontology(space_id)
    except Exception as exc:  # noqa: BLE001 - named in the log, catalog stays empty
        _LOG.warning("space_catalog_ontology_unreadable %s", type(exc).__name__)
        return None


def session_space_catalog(
    onto: Ontology | None,
    granted_tables: Sequence[str],
) -> dict[str, Any]:
    """Approved objects, links, measures, definitions, and granted tables.

    An object is included only when its name or relation is a granted table.
    Links and measures are included only when both ends, or the grain, were
    included. Nothing is added from the demo ontology.
    """
    by_fold: dict[str, str] = {}
    for raw in granted_tables:
        text = str(raw or "").strip()
        if text:
            by_fold.setdefault(_fold(text), text)
    granted_list = [by_fold[key] for key in sorted(by_fold)]
    granted_fold = set(by_fold)
    objects: list[dict[str, Any]] = []
    if onto is not None:
        for _name, obj in sorted(onto.objects.items(), key=lambda kv: _fold(kv[0])):
            if _fold(obj.name) not in granted_fold and _fold(obj.relation) not in granted_fold:
                continue
            objects.append(
                {
                    "name": obj.name,
                    "relation": obj.relation,
                    "key": list(obj.key),
                }
            )
    object_names = {item["name"] for item in objects}
    links: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    definitions: list[dict[str, Any]] = []
    if onto is not None:
        for _name, link in sorted(onto.links.items(), key=lambda kv: _fold(kv[0])):
            if link.from_object not in object_names or link.to_object not in object_names:
                continue
            links.append(
                {
                    "name": link.name,
                    "from_object": link.from_object,
                    "from_columns": list(link.from_columns),
                    "to_object": link.to_object,
                    "to_columns": list(link.to_columns),
                    "cardinality": link.cardinality,
                }
            )
        for _name, measure in sorted(onto.measures.items(), key=lambda kv: _fold(kv[0])):
            if measure.grain not in object_names:
                continue
            measures.append(
                {
                    "name": measure.name,
                    "grain": measure.grain,
                    "expression": measure.expression,
                    "description": measure.description,
                }
            )
            text = measure.expression.strip() or measure.description.strip()
            definitions.append(
                {
                    "name": measure.name,
                    "grain": measure.grain,
                    "text": text,
                }
            )
    return {
        "objects": objects,
        "links": links,
        "measures": measures,
        "definitions": definitions,
        "granted_tables": granted_list,
    }


def catalog_prompt(catalog: Mapping[str, Any]) -> str:
    """The catalog bytes Cortex is given. One JSON object, keys sorted."""
    return json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def suggestions_for(catalog: Mapping[str, Any]) -> list[str]:
    """Measure names of this catalog. No other Space, no demo metric."""
    out: list[str] = []
    measures = catalog.get("measures")
    if not isinstance(measures, list):
        return out
    for item in measures:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def handoff_reason(catalog: Mapping[str, Any]) -> str:
    """Typed reason. Names only this catalog's tables and measures."""
    tables_raw = catalog.get("granted_tables")
    tables = [str(t) for t in tables_raw] if isinstance(tables_raw, list) else []
    listed = ", ".join(tables) if tables else "none"
    names = suggestions_for(catalog)
    if not names:
        return (
            "no_declared_measure. This Space has no approved measure. "
            f"Granted tables: {listed}."
        )
    return (
        "space_catalog. Approved measures on this Space: "
        f"{', '.join(names)}. Granted tables: {listed}."
    )


def stamp_manifest(manifest: Any, catalog: Mapping[str, Any]) -> Any:
    """Copy ``manifest`` and attach the catalog off the signed field set."""
    copied = manifest.model_copy()
    object.__setattr__(copied, _ATTR, dict(catalog))
    return copied


def catalog_from_manifest(manifest: Any) -> dict[str, Any] | None:
    raw = getattr(manifest, _ATTR, None)
    return dict(raw) if isinstance(raw, dict) else None


def generative_handoff(payload: object) -> bool:
    """True when Cortex handed the question to the DMS generative path."""
    if not isinstance(payload, dict):
        return False
    return payload.get(HANDOFF_FIELD) == HANDOFF_GENERATIVE


def release_chooser_handoff(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """A handoff is not an answer while the flag is on.

    Flag off returns ``payload`` unchanged, including when the key is present.
    """
    if space_catalog_enabled() and generative_handoff(payload):
        return None
    return payload


def handoff_reconfirm(
    catalog: Mapping[str, Any],
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    """Typed reconfirm after generative produced no SQL. No figure."""
    from dms_executor.sql_vote import reconfirm_envelope

    env = reconfirm_envelope(
        question=question,
        why=handoff_reason(catalog),
        closest_question=question,
        space_id=space_id,
        session_id=session_id,
    )
    env["suggestions"] = suggestions_for(catalog)
    return env


def space_catalog_after_ask(
    resp: object,
    *,
    question: str,
    space_id: str | None,
    session_id: str | None,
    granted: Sequence[str],
    ontology: Ontology | None,
) -> dict[str, Any] | None:
    """The generative handoff envelope, or None to keep the ask path as it was.

    None when the flag is off, the Space is the demo Space, or Cortex did not
    hand off. The caller then maps the response the way it always has.
    """
    if not space_catalog_enabled() or is_demo_space(space_id):
        return None
    if str(getattr(resp, "space_catalog_handoff", None) or "") != HANDOFF_GENERATIVE:
        return None
    catalog = session_space_catalog(ontology, granted)
    return handoff_reconfirm(
        catalog,
        question=question,
        space_id=space_id,
        session_id=session_id,
    )
