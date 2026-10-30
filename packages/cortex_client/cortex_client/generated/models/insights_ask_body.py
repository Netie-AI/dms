from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InsightsAskBody")



@_attrs_define
class InsightsAskBody:
    """ 
        Attributes:
            ask (bool | Unset):  Default: True.
            consumer (str | Unset):  Default: 'dms'.
            generate (bool | Unset):  Default: False.
            intent (str | Unset):  Default: ''.
            question (str | Unset):  Default: ''.
            session_id (str | Unset):  Default: 'demo'.
            space_id (None | str | Unset):
            schema_context (None | str | Unset): Caller shortlist. Replaces the pack table list when present. Pick reasons
                live in this string, not in a separate field: table lines use reason=score:<score>, column lines may include
                reason=<why>, join lines include reason=<why>. maxLength 8192. The engine cap is 8192 UTF-8 bytes.
     """

    ask: bool | Unset = True
    consumer: str | Unset = 'dms'
    generate: bool | Unset = False
    intent: str | Unset = ''
    question: str | Unset = ''
    session_id: str | Unset = 'demo'
    space_id: None | str | Unset = UNSET
    schema_context: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        ask = self.ask

        consumer = self.consumer

        generate = self.generate

        intent = self.intent

        question = self.question

        session_id = self.session_id

        space_id: None | str | Unset
        if isinstance(self.space_id, Unset):
            space_id = UNSET
        else:
            space_id = self.space_id

        schema_context: None | str | Unset
        if isinstance(self.schema_context, Unset):
            schema_context = UNSET
        else:
            schema_context = self.schema_context


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
        })
        if ask is not UNSET:
            field_dict["ask"] = ask
        if consumer is not UNSET:
            field_dict["consumer"] = consumer
        if generate is not UNSET:
            field_dict["generate"] = generate
        if intent is not UNSET:
            field_dict["intent"] = intent
        if question is not UNSET:
            field_dict["question"] = question
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if space_id is not UNSET:
            field_dict["space_id"] = space_id
        if schema_context is not UNSET:
            field_dict["schema_context"] = schema_context

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        ask = d.pop("ask", UNSET)

        consumer = d.pop("consumer", UNSET)

        generate = d.pop("generate", UNSET)

        intent = d.pop("intent", UNSET)

        question = d.pop("question", UNSET)

        session_id = d.pop("session_id", UNSET)

        def _parse_space_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        space_id = _parse_space_id(d.pop("space_id", UNSET))


        def _parse_schema_context(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schema_context = _parse_schema_context(d.pop("schema_context", UNSET))


        insights_ask_body = cls(
            ask=ask,
            consumer=consumer,
            generate=generate,
            intent=intent,
            question=question,
            session_id=session_id,
            space_id=space_id,
            schema_context=schema_context,
        )


        insights_ask_body.additional_properties = d
        return insights_ask_body

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
