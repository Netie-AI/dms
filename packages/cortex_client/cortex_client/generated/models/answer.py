from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
  from ..models.answer_model_calls_type_0 import AnswerModelCallsType0
  from ..models.answer_rows_type_0_item import AnswerRowsType0Item
  from ..models.contributing_source import ContributingSource
  from ..models.memory_read import MemoryRead
  from ..models.plan_sql_reconfirm import PlanSqlReconfirm
  from ..models.provenance import Provenance





T = TypeVar("T", bound="Answer")



@_attrs_define
class Answer:
    """ 
        Attributes:
            answer (str):
            audit_id (str):
            provenance (Provenance):
            route (str):
            answer_id (None | str | Unset):
            assumptions (list[str] | Unset):
            contributing_sources (list[ContributingSource] | Unset):
            drillthrough_token (None | str | Unset):
            memory_ids_read (list[str] | Unset):
            memory_reads (list[MemoryRead] | Unset):
            model_calls (AnswerModelCallsType0 | None | Unset): 1.5.0 logical FreeRoute complete() calls per ladder step,
                plus total. One count per complete() the ladder issues. Transport or proxy retries (an upstream 429 retried by
                the cap proxy or the FreeRoute client) are not counted. Keys that ran: plan, error-fed-retry-<N> (N starts at
                1), stronger-model, reconfirm. total is their sum. DMS reads per-step counts only from this object. Null when
                this path made no model call. 1.4 clients ignore it.
            plan_sql_rung (None | str | Unset):
            reconfirm (None | PlanSqlReconfirm | Unset):
            reused (bool | Unset):  Default: False.
            row_count (int | None | Unset):
            rows (list[AnswerRowsType0Item] | None | Unset):
            served_local (bool | Unset):  Default: False.
            served_model (None | str | Unset):
            served_provider (None | str | Unset):
            served_reason (None | str | Unset):
            sql_used (None | str | Unset):
            suggestions (list[str] | Unset):
     """

    answer: str
    audit_id: str
    provenance: Provenance
    route: str
    answer_id: None | str | Unset = UNSET
    assumptions: list[str] | Unset = UNSET
    contributing_sources: list[ContributingSource] | Unset = UNSET
    drillthrough_token: None | str | Unset = UNSET
    memory_ids_read: list[str] | Unset = UNSET
    memory_reads: list[MemoryRead] | Unset = UNSET
    model_calls: AnswerModelCallsType0 | None | Unset = UNSET
    plan_sql_rung: None | str | Unset = UNSET
    reconfirm: None | PlanSqlReconfirm | Unset = UNSET
    reused: bool | Unset = False
    row_count: int | None | Unset = UNSET
    rows: list[AnswerRowsType0Item] | None | Unset = UNSET
    served_local: bool | Unset = False
    served_model: None | str | Unset = UNSET
    served_provider: None | str | Unset = UNSET
    served_reason: None | str | Unset = UNSET
    sql_used: None | str | Unset = UNSET
    suggestions: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.answer_model_calls_type_0 import AnswerModelCallsType0
        from ..models.plan_sql_reconfirm import PlanSqlReconfirm
        answer = self.answer

        audit_id = self.audit_id

        provenance = self.provenance.to_dict()

        route = self.route

        answer_id: None | str | Unset
        if isinstance(self.answer_id, Unset):
            answer_id = UNSET
        else:
            answer_id = self.answer_id

        assumptions: list[str] | Unset = UNSET
        if not isinstance(self.assumptions, Unset):
            assumptions = self.assumptions



        contributing_sources: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.contributing_sources, Unset):
            contributing_sources = []
            for contributing_sources_item_data in self.contributing_sources:
                contributing_sources_item = contributing_sources_item_data.to_dict()
                contributing_sources.append(contributing_sources_item)



        drillthrough_token: None | str | Unset
        if isinstance(self.drillthrough_token, Unset):
            drillthrough_token = UNSET
        else:
            drillthrough_token = self.drillthrough_token

        memory_ids_read: list[str] | Unset = UNSET
        if not isinstance(self.memory_ids_read, Unset):
            memory_ids_read = self.memory_ids_read



        memory_reads: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.memory_reads, Unset):
            memory_reads = []
            for memory_reads_item_data in self.memory_reads:
                memory_reads_item = memory_reads_item_data.to_dict()
                memory_reads.append(memory_reads_item)



        model_calls: dict[str, Any] | None | Unset
        if isinstance(self.model_calls, Unset):
            model_calls = UNSET
        elif isinstance(self.model_calls, AnswerModelCallsType0):
            model_calls = self.model_calls.to_dict()
        else:
            model_calls = self.model_calls

        plan_sql_rung: None | str | Unset
        if isinstance(self.plan_sql_rung, Unset):
            plan_sql_rung = UNSET
        else:
            plan_sql_rung = self.plan_sql_rung

        reconfirm: dict[str, Any] | None | Unset
        if isinstance(self.reconfirm, Unset):
            reconfirm = UNSET
        elif isinstance(self.reconfirm, PlanSqlReconfirm):
            reconfirm = self.reconfirm.to_dict()
        else:
            reconfirm = self.reconfirm

        reused = self.reused

        row_count: int | None | Unset
        if isinstance(self.row_count, Unset):
            row_count = UNSET
        else:
            row_count = self.row_count

        rows: list[dict[str, Any]] | None | Unset
        if isinstance(self.rows, Unset):
            rows = UNSET
        elif isinstance(self.rows, list):
            rows = []
            for rows_type_0_item_data in self.rows:
                rows_type_0_item = rows_type_0_item_data.to_dict()
                rows.append(rows_type_0_item)


        else:
            rows = self.rows

        served_local = self.served_local

        served_model: None | str | Unset
        if isinstance(self.served_model, Unset):
            served_model = UNSET
        else:
            served_model = self.served_model

        served_provider: None | str | Unset
        if isinstance(self.served_provider, Unset):
            served_provider = UNSET
        else:
            served_provider = self.served_provider

        served_reason: None | str | Unset
        if isinstance(self.served_reason, Unset):
            served_reason = UNSET
        else:
            served_reason = self.served_reason

        sql_used: None | str | Unset
        if isinstance(self.sql_used, Unset):
            sql_used = UNSET
        else:
            sql_used = self.sql_used

        suggestions: list[str] | Unset = UNSET
        if not isinstance(self.suggestions, Unset):
            suggestions = self.suggestions




        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "answer": answer,
            "audit_id": audit_id,
            "provenance": provenance,
            "route": route,
        })
        if answer_id is not UNSET:
            field_dict["answer_id"] = answer_id
        if assumptions is not UNSET:
            field_dict["assumptions"] = assumptions
        if contributing_sources is not UNSET:
            field_dict["contributing_sources"] = contributing_sources
        if drillthrough_token is not UNSET:
            field_dict["drillthrough_token"] = drillthrough_token
        if memory_ids_read is not UNSET:
            field_dict["memory_ids_read"] = memory_ids_read
        if memory_reads is not UNSET:
            field_dict["memory_reads"] = memory_reads
        if model_calls is not UNSET:
            field_dict["model_calls"] = model_calls
        if plan_sql_rung is not UNSET:
            field_dict["plan_sql_rung"] = plan_sql_rung
        if reconfirm is not UNSET:
            field_dict["reconfirm"] = reconfirm
        if reused is not UNSET:
            field_dict["reused"] = reused
        if row_count is not UNSET:
            field_dict["row_count"] = row_count
        if rows is not UNSET:
            field_dict["rows"] = rows
        if served_local is not UNSET:
            field_dict["served_local"] = served_local
        if served_model is not UNSET:
            field_dict["served_model"] = served_model
        if served_provider is not UNSET:
            field_dict["served_provider"] = served_provider
        if served_reason is not UNSET:
            field_dict["served_reason"] = served_reason
        if sql_used is not UNSET:
            field_dict["sql_used"] = sql_used
        if suggestions is not UNSET:
            field_dict["suggestions"] = suggestions

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.answer_model_calls_type_0 import AnswerModelCallsType0
        from ..models.answer_rows_type_0_item import AnswerRowsType0Item
        from ..models.contributing_source import ContributingSource
        from ..models.memory_read import MemoryRead
        from ..models.plan_sql_reconfirm import PlanSqlReconfirm
        from ..models.provenance import Provenance
        d = dict(src_dict)
        answer = d.pop("answer")

        audit_id = d.pop("audit_id")

        provenance = Provenance.from_dict(d.pop("provenance"))




        route = d.pop("route")

        def _parse_answer_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        answer_id = _parse_answer_id(d.pop("answer_id", UNSET))


        assumptions = cast(list[str], d.pop("assumptions", UNSET))


        _contributing_sources = d.pop("contributing_sources", UNSET)
        contributing_sources: list[ContributingSource] | Unset = UNSET
        if _contributing_sources is not UNSET:
            contributing_sources = []
            for contributing_sources_item_data in _contributing_sources:
                contributing_sources_item = ContributingSource.from_dict(contributing_sources_item_data)



                contributing_sources.append(contributing_sources_item)


        def _parse_drillthrough_token(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        drillthrough_token = _parse_drillthrough_token(d.pop("drillthrough_token", UNSET))


        memory_ids_read = cast(list[str], d.pop("memory_ids_read", UNSET))


        _memory_reads = d.pop("memory_reads", UNSET)
        memory_reads: list[MemoryRead] | Unset = UNSET
        if _memory_reads is not UNSET:
            memory_reads = []
            for memory_reads_item_data in _memory_reads:
                memory_reads_item = MemoryRead.from_dict(memory_reads_item_data)



                memory_reads.append(memory_reads_item)


        def _parse_model_calls(data: object) -> AnswerModelCallsType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                model_calls_type_0 = AnswerModelCallsType0.from_dict(data)



                return model_calls_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AnswerModelCallsType0 | None | Unset, data)

        model_calls = _parse_model_calls(d.pop("model_calls", UNSET))


        def _parse_plan_sql_rung(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        plan_sql_rung = _parse_plan_sql_rung(d.pop("plan_sql_rung", UNSET))


        def _parse_reconfirm(data: object) -> None | PlanSqlReconfirm | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reconfirm_type_0 = PlanSqlReconfirm.from_dict(data)



                return reconfirm_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PlanSqlReconfirm | Unset, data)

        reconfirm = _parse_reconfirm(d.pop("reconfirm", UNSET))


        reused = d.pop("reused", UNSET)

        def _parse_row_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        row_count = _parse_row_count(d.pop("row_count", UNSET))


        def _parse_rows(data: object) -> list[AnswerRowsType0Item] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                rows_type_0 = []
                _rows_type_0 = data
                for rows_type_0_item_data in (_rows_type_0):
                    rows_type_0_item = AnswerRowsType0Item.from_dict(rows_type_0_item_data)



                    rows_type_0.append(rows_type_0_item)

                return rows_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[AnswerRowsType0Item] | None | Unset, data)

        rows = _parse_rows(d.pop("rows", UNSET))


        served_local = d.pop("served_local", UNSET)

        def _parse_served_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        served_model = _parse_served_model(d.pop("served_model", UNSET))


        def _parse_served_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        served_provider = _parse_served_provider(d.pop("served_provider", UNSET))


        def _parse_served_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        served_reason = _parse_served_reason(d.pop("served_reason", UNSET))


        def _parse_sql_used(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sql_used = _parse_sql_used(d.pop("sql_used", UNSET))


        suggestions = cast(list[str], d.pop("suggestions", UNSET))


        answer = cls(
            answer=answer,
            audit_id=audit_id,
            provenance=provenance,
            route=route,
            answer_id=answer_id,
            assumptions=assumptions,
            contributing_sources=contributing_sources,
            drillthrough_token=drillthrough_token,
            memory_ids_read=memory_ids_read,
            memory_reads=memory_reads,
            model_calls=model_calls,
            plan_sql_rung=plan_sql_rung,
            reconfirm=reconfirm,
            reused=reused,
            row_count=row_count,
            rows=rows,
            served_local=served_local,
            served_model=served_model,
            served_provider=served_provider,
            served_reason=served_reason,
            sql_used=sql_used,
            suggestions=suggestions,
        )


        answer.additional_properties = d
        return answer

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
