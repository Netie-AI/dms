from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ask_request_plan_sql_confirm_type_0 import AskRequestPlanSqlConfirmType0
from ..types import UNSET, Unset

if TYPE_CHECKING:
  from ..models.ask_payload import AskPayload





T = TypeVar("T", bound="AskRequest")



@_attrs_define
class AskRequest:
    """ Ask plane request.

    Unknown keys are ignored, as on main. ``model``, ``strict`` and
    ``provider`` are rejected by name before any model call. Routing stays
    in OpenVault's route config. Full request strictness is a breaking
    change tracked in #388.

        Attributes:
            question (str):
            dms_payload (AskPayload | None | Unset):
            plan_sql_confirm (AskRequestPlanSqlConfirmType0 | None | Unset):
            schema_context (None | str | Unset): 1.9.0 caller shortlist, the SCHEMA-CONTEXT-01 string POST /v1/insights
                takes. Read only when the engine has its schema_context switch on (off by default). Off, only its declared type
                (string or null, maxLength 8192) is checked and the answer is the one the same ask gets without this field. On,
                it is used only by the plan+SQL path (dms_payload set and that path switched on): its SQL prompt is then the
                signed grant intersected with the tables, columns and joins this string declares, and a table outside the grant
                is dropped. Absent or blank: the dms_payload schema is the prompt, as on 1.7. A value the insights rule refuses
                (over 8192 UTF-8 bytes, a control or format character, a line outside the shapes it parses) is HTTP 422 with
                that code before any model call. A string that declares no table, or no granted table, is a named abstain.
                maxLength 8192; the engine cap is 8192 UTF-8 bytes.
            scored_pack_id (None | str | Unset):
            session_id (str | Unset):  Default: 'demo'.
            space_id (None | str | Unset):
     """

    question: str
    dms_payload: AskPayload | None | Unset = UNSET
    plan_sql_confirm: AskRequestPlanSqlConfirmType0 | None | Unset = UNSET
    schema_context: None | str | Unset = UNSET
    scored_pack_id: None | str | Unset = UNSET
    session_id: str | Unset = 'demo'
    space_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.ask_payload import AskPayload
        question = self.question

        dms_payload: dict[str, Any] | None | Unset
        if isinstance(self.dms_payload, Unset):
            dms_payload = UNSET
        elif isinstance(self.dms_payload, AskPayload):
            dms_payload = self.dms_payload.to_dict()
        else:
            dms_payload = self.dms_payload

        plan_sql_confirm: None | str | Unset
        if isinstance(self.plan_sql_confirm, Unset):
            plan_sql_confirm = UNSET
        elif isinstance(self.plan_sql_confirm, AskRequestPlanSqlConfirmType0):
            plan_sql_confirm = self.plan_sql_confirm.value
        else:
            plan_sql_confirm = self.plan_sql_confirm

        schema_context: None | str | Unset
        if isinstance(self.schema_context, Unset):
            schema_context = UNSET
        else:
            schema_context = self.schema_context

        scored_pack_id: None | str | Unset
        if isinstance(self.scored_pack_id, Unset):
            scored_pack_id = UNSET
        else:
            scored_pack_id = self.scored_pack_id

        session_id = self.session_id

        space_id: None | str | Unset
        if isinstance(self.space_id, Unset):
            space_id = UNSET
        else:
            space_id = self.space_id


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "question": question,
        })
        if dms_payload is not UNSET:
            field_dict["dms_payload"] = dms_payload
        if plan_sql_confirm is not UNSET:
            field_dict["plan_sql_confirm"] = plan_sql_confirm
        if schema_context is not UNSET:
            field_dict["schema_context"] = schema_context
        if scored_pack_id is not UNSET:
            field_dict["scored_pack_id"] = scored_pack_id
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if space_id is not UNSET:
            field_dict["space_id"] = space_id

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ask_payload import AskPayload
        d = dict(src_dict)
        question = d.pop("question")

        def _parse_dms_payload(data: object) -> AskPayload | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                dms_payload_type_0 = AskPayload.from_dict(data)



                return dms_payload_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AskPayload | None | Unset, data)

        dms_payload = _parse_dms_payload(d.pop("dms_payload", UNSET))


        def _parse_plan_sql_confirm(data: object) -> AskRequestPlanSqlConfirmType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                plan_sql_confirm_type_0 = AskRequestPlanSqlConfirmType0(data)



                return plan_sql_confirm_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AskRequestPlanSqlConfirmType0 | None | Unset, data)

        plan_sql_confirm = _parse_plan_sql_confirm(d.pop("plan_sql_confirm", UNSET))


        def _parse_schema_context(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schema_context = _parse_schema_context(d.pop("schema_context", UNSET))


        def _parse_scored_pack_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        scored_pack_id = _parse_scored_pack_id(d.pop("scored_pack_id", UNSET))


        session_id = d.pop("session_id", UNSET)

        def _parse_space_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        space_id = _parse_space_id(d.pop("space_id", UNSET))


        ask_request = cls(
            question=question,
            dms_payload=dms_payload,
            plan_sql_confirm=plan_sql_confirm,
            schema_context=schema_context,
            scored_pack_id=scored_pack_id,
            session_id=session_id,
            space_id=space_id,
        )


        ask_request.additional_properties = d
        return ask_request

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
