"""CHAT-01 Studio turn. Copy Cortex fields. Do not invent a plan.

Pre-SQL preview is Cortex ``GET /v1/insights/ontology`` (no ask, no generate).
That body has ``locations`` / ``joins`` / ``metrics``. It has no ``query_plan``
and no clarify-question list.

``query_plan`` is copied only when a payload already carries a measure-bearing
object (contract ``DMSQueryResponse.query_plan``, or the Insights generative
nest the client already reads). A law sentence or ``answer`` string is not a plan.

``needs_clarification`` is a route string. The engine puts the clarify sentence
in ``answer`` and nearest asks in ``suggestions``. There is no question list.
``suggestions`` stay follow-ups after a result, not a block.
"""

from __future__ import annotations

from typing import Any

NOT_PROVIDED = "not provided by Cortex"

_JOIN_KEYS = ("id", "from", "to", "from_property", "to_property")


def _json_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_copy(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _measure_plan(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and str(raw.get("measure") or "").strip():
        copied = _json_copy(raw)
        return copied if isinstance(copied, dict) else None
    return None


def typed_query_plan(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Same nests as ``cortex_client.compute.typed_query_plan``. No new plan."""
    found = _measure_plan(payload.get("query_plan"))
    if found is not None:
        return found
    gen = payload.get("generative")
    if isinstance(gen, dict):
        found = _measure_plan(gen.get("query_plan")) or _measure_plan(gen.get("plan"))
        if found is not None:
            return found
        climb = gen.get("climb")
        if isinstance(climb, dict):
            found = _measure_plan(climb.get("query_plan")) or _measure_plan(climb.get("plan"))
            if found is not None:
                return found
    climb = payload.get("climb")
    if isinstance(climb, dict):
        return _measure_plan(climb.get("query_plan")) or _measure_plan(climb.get("plan"))
    return None


def _ranking(payload: dict[str, Any]) -> dict[str, Any] | None:
    onto = payload.get("ontology")
    if isinstance(onto, dict) and any(k in onto for k in ("locations", "joins", "metrics")):
        return onto
    if any(k in payload for k in ("locations", "joins", "metrics")):
        return payload
    return None


def _missing() -> dict[str, Any]:
    return {"provided": False, "text": NOT_PROVIDED, "items": []}


def _tables(ranking: dict[str, Any] | None) -> dict[str, Any]:
    if ranking is None or "locations" not in ranking:
        return _missing()
    raw = ranking.get("locations")
    items: list[str] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            where = row.get("where")
            table = ""
            if isinstance(where, dict):
                table = str(where.get("table") or "").strip()
            if not table:
                table = str(row.get("id") or "").strip()
            if table and table not in items:
                items.append(table)
    return {"provided": True, "text": None, "items": items}


def _joins(ranking: dict[str, Any] | None) -> dict[str, Any]:
    if ranking is None or "joins" not in ranking:
        return _missing()
    raw = ranking.get("joins")
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            item = {key: row[key] for key in _JOIN_KEYS if key in row}
            if item:
                items.append(_json_copy(item))
    return {"provided": True, "text": None, "items": items}


def _metrics(ranking: dict[str, Any] | None) -> dict[str, Any]:
    if ranking is None or "metrics" not in ranking:
        return _missing()
    raw = ranking.get("metrics")
    items: list[str] = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict):
                mid = str(row.get("id") or "").strip()
            elif isinstance(row, str):
                mid = row.strip()
            else:
                mid = ""
            if mid and mid not in items:
                items.append(mid)
    return {"provided": True, "text": None, "items": items}


def _clarify(payload: dict[str, Any]) -> dict[str, Any]:
    """Block only on the real route plus a non-empty ``answer`` sentence."""
    if str(payload.get("route") or "") != "needs_clarification":
        return {"provided": False, "text": NOT_PROVIDED, "blocks": False}
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return {"provided": False, "text": NOT_PROVIDED, "blocks": False}
    return {"provided": True, "text": answer.strip(), "blocks": True}


def project_studio_turn(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Three slots. Missing Cortex fields stay ``not provided by Cortex``."""
    body = payload if isinstance(payload, dict) else {}
    plan = typed_query_plan(body)
    ranking = _ranking(body)
    clarify = _clarify(body)
    if plan is None:
        plan_slot: dict[str, Any] = {
            "provided": False,
            "text": NOT_PROVIDED,
            "query_plan": None,
        }
    else:
        plan_slot = {"provided": True, "text": None, "query_plan": plan}
    return {
        "sql_ran": False,
        "plan": plan_slot,
        "ontology": {
            "tables": _tables(ranking),
            "joins": _joins(ranking),
            "metrics": _metrics(ranking),
        },
        "clarify": clarify,
    }


def confirm_allowed(preview: dict[str, Any] | None, reply: str) -> bool:
    """False when clarify blocks and the reply is empty. Unknown shape fails closed."""
    if not isinstance(preview, dict):
        return False
    clarify = preview.get("clarify")
    if not isinstance(clarify, dict) or "blocks" not in clarify:
        return False
    if clarify.get("blocks") is True:
        return bool(str(reply or "").strip())
    if clarify.get("blocks") is False:
        return True
    return False


def question_to_execute(question: str, reply: str, *, blocks: bool) -> str:
    """User words only. A blocking reply is appended; nothing is invented."""
    asked = str(question or "").strip()
    answered = str(reply or "").strip()
    if blocks and answered:
        return f"{asked}\n{answered}"
    return asked


def followups_from_envelope(envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Contract ``Answer.suggestions`` after a result. Missing key is not a list."""
    if not isinstance(envelope, dict) or "suggestions" not in envelope:
        return {"provided": False, "text": NOT_PROVIDED, "items": []}
    raw = envelope.get("suggestions")
    if not isinstance(raw, list):
        return {"provided": False, "text": NOT_PROVIDED, "items": []}
    items = [str(item) for item in raw if isinstance(item, str) and str(item).strip()]
    return {"provided": True, "text": None, "items": items}


class StudioChat:
    """Plan, edit, clarify, confirm. ``confirm`` is the only step that counts as a run."""

    def __init__(self) -> None:
        self.question = ""
        self.preview: dict[str, Any] | None = None
        self.reply = ""
        self.executions = 0
        self.plan_calls = 0
        self.executed_question: str | None = None

    def plan(self, question: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        self.question = str(question or "").strip()
        self.preview = project_studio_turn(payload)
        self.reply = ""
        self.plan_calls += 1
        return self.preview

    def edit(self, question: str) -> None:
        """Drop the previous preview. The next ``plan`` is the re-plan. No run."""
        self.question = str(question or "").strip()
        self.preview = None
        self.reply = ""

    def answer(self, reply: str) -> None:
        self.reply = str(reply or "")

    def confirm(self) -> str:
        if self.preview is None:
            raise RuntimeError("confirm before plan")
        if not confirm_allowed(self.preview, self.reply):
            raise RuntimeError("clarify unanswered")
        blocks = bool(self.preview["clarify"]["blocks"])
        text = question_to_execute(self.question, self.reply, blocks=blocks)
        self.executions += 1
        self.executed_question = text
        return text
