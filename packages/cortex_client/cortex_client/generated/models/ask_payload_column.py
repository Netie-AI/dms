from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AskPayloadColumn")



@_attrs_define
class AskPayloadColumn:
    """ 1.5.0: one column of a table the consumer selected for this ask.

        Attributes:
            name (str):
            description (None | str | Unset):
            masked (bool | None | Unset): 1.9.0 the consumer's mask state for this column. The plan+SQL path samples
                distinct values of a column into its model prompt only when this is false. True or absent: the column's name and
                type may reach the prompt, its values never do. A schema_context column this payload does not list has no mask
                state and is not sampled.
            type_ (None | str | Unset):
     """

    name: str
    description: None | str | Unset = UNSET
    masked: bool | None | Unset = UNSET
    type_: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        name = self.name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        masked: bool | None | Unset
        if isinstance(self.masked, Unset):
            masked = UNSET
        else:
            masked = self.masked

        type_: None | str | Unset
        if isinstance(self.type_, Unset):
            type_ = UNSET
        else:
            type_ = self.type_


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "name": name,
        })
        if description is not UNSET:
            field_dict["description"] = description
        if masked is not UNSET:
            field_dict["masked"] = masked
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))


        def _parse_masked(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        masked = _parse_masked(d.pop("masked", UNSET))


        def _parse_type_(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        type_ = _parse_type_(d.pop("type", UNSET))


        ask_payload_column = cls(
            name=name,
            description=description,
            masked=masked,
            type_=type_,
        )


        ask_payload_column.additional_properties = d
        return ask_payload_column

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
