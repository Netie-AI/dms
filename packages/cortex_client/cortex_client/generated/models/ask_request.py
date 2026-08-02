from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AskRequest")



@_attrs_define
class AskRequest:
    """ 
        Attributes:
            question (str):
            session_id (str | Unset):  Default: 'demo'.
            space_id (None | str | Unset):
     """

    question: str
    session_id: str | Unset = 'demo'
    space_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        question = self.question

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
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if space_id is not UNSET:
            field_dict["space_id"] = space_id

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        question = d.pop("question")

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
