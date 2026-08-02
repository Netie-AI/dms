from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
  from ..models.drillthrough_response_rows_item import DrillthroughResponseRowsItem





T = TypeVar("T", bound="DrillthroughResponse")



@_attrs_define
class DrillthroughResponse:
    """ 
        Attributes:
            answer_id (str):
            row_count (int):
            session_id (str):
            sql_used (str):
            approximate (bool | Unset):  Default: False.
            rows (list[DrillthroughResponseRowsItem] | Unset):
            total_count (int | None | Unset):
     """

    answer_id: str
    row_count: int
    session_id: str
    sql_used: str
    approximate: bool | Unset = False
    rows: list[DrillthroughResponseRowsItem] | Unset = UNSET
    total_count: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        answer_id = self.answer_id

        row_count = self.row_count

        session_id = self.session_id

        sql_used = self.sql_used

        approximate = self.approximate

        rows: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.rows, Unset):
            rows = []
            for rows_item_data in self.rows:
                rows_item = rows_item_data.to_dict()
                rows.append(rows_item)



        total_count: int | None | Unset
        if isinstance(self.total_count, Unset):
            total_count = UNSET
        else:
            total_count = self.total_count


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "answer_id": answer_id,
            "row_count": row_count,
            "session_id": session_id,
            "sql_used": sql_used,
        })
        if approximate is not UNSET:
            field_dict["approximate"] = approximate
        if rows is not UNSET:
            field_dict["rows"] = rows
        if total_count is not UNSET:
            field_dict["total_count"] = total_count

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.drillthrough_response_rows_item import DrillthroughResponseRowsItem
        d = dict(src_dict)
        answer_id = d.pop("answer_id")

        row_count = d.pop("row_count")

        session_id = d.pop("session_id")

        sql_used = d.pop("sql_used")

        approximate = d.pop("approximate", UNSET)

        _rows = d.pop("rows", UNSET)
        rows: list[DrillthroughResponseRowsItem] | Unset = UNSET
        if _rows is not UNSET:
            rows = []
            for rows_item_data in _rows:
                rows_item = DrillthroughResponseRowsItem.from_dict(rows_item_data)



                rows.append(rows_item)


        def _parse_total_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        total_count = _parse_total_count(d.pop("total_count", UNSET))


        drillthrough_response = cls(
            answer_id=answer_id,
            row_count=row_count,
            session_id=session_id,
            sql_used=sql_used,
            approximate=approximate,
            rows=rows,
            total_count=total_count,
        )


        drillthrough_response.additional_properties = d
        return drillthrough_response

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
