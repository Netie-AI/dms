from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AskPayloadJoin")



@_attrs_define
class AskPayloadJoin:
    """ 1.5.0: one ontology join between two selected tables.

        Attributes:
            left_column (str):
            left_table (str):
            right_column (str):
            right_table (str):
            relation (None | str | Unset):
     """

    left_column: str
    left_table: str
    right_column: str
    right_table: str
    relation: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        left_column = self.left_column

        left_table = self.left_table

        right_column = self.right_column

        right_table = self.right_table

        relation: None | str | Unset
        if isinstance(self.relation, Unset):
            relation = UNSET
        else:
            relation = self.relation


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "left_column": left_column,
            "left_table": left_table,
            "right_column": right_column,
            "right_table": right_table,
        })
        if relation is not UNSET:
            field_dict["relation"] = relation

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        left_column = d.pop("left_column")

        left_table = d.pop("left_table")

        right_column = d.pop("right_column")

        right_table = d.pop("right_table")

        def _parse_relation(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        relation = _parse_relation(d.pop("relation", UNSET))


        ask_payload_join = cls(
            left_column=left_column,
            left_table=left_table,
            right_column=right_column,
            right_table=right_table,
            relation=relation,
        )


        ask_payload_join.additional_properties = d
        return ask_payload_join

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
