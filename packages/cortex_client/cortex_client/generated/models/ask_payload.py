from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
  from ..models.ask_payload_join import AskPayloadJoin
  from ..models.ask_payload_table import AskPayloadTable





T = TypeVar("T", bound="AskPayload")



@_attrs_define
class AskPayload:
    """ 1.5.0: selected tables, their schema and ontology joins for one question.

    The payload can only narrow what the signed grant allows: a table or join
    outside the grant is refused before any model call.

        Attributes:
            tables (list[AskPayloadTable]):
            joins (list[AskPayloadJoin] | Unset):
     """

    tables: list[AskPayloadTable]
    joins: list[AskPayloadJoin] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        tables = []
        for tables_item_data in self.tables:
            tables_item = tables_item_data.to_dict()
            tables.append(tables_item)



        joins: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.joins, Unset):
            joins = []
            for joins_item_data in self.joins:
                joins_item = joins_item_data.to_dict()
                joins.append(joins_item)




        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "tables": tables,
        })
        if joins is not UNSET:
            field_dict["joins"] = joins

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ask_payload_join import AskPayloadJoin
        from ..models.ask_payload_table import AskPayloadTable
        d = dict(src_dict)
        tables = []
        _tables = d.pop("tables")
        for tables_item_data in (_tables):
            tables_item = AskPayloadTable.from_dict(tables_item_data)



            tables.append(tables_item)


        _joins = d.pop("joins", UNSET)
        joins: list[AskPayloadJoin] | Unset = UNSET
        if _joins is not UNSET:
            joins = []
            for joins_item_data in _joins:
                joins_item = AskPayloadJoin.from_dict(joins_item_data)



                joins.append(joins_item)


        ask_payload = cls(
            tables=tables,
            joins=joins,
        )


        ask_payload.additional_properties = d
        return ask_payload

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
