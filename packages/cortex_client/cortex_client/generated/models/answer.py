from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast

if TYPE_CHECKING:
  from ..models.answer_rows_type_0_item import AnswerRowsType0Item
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
            row_count (int | None | Unset):
            rows (list[AnswerRowsType0Item] | None | Unset):
            sql_used (None | str | Unset):
            suggestions (list[str] | Unset):
     """

    answer: str
    audit_id: str
    provenance: Provenance
    route: str
    row_count: int | None | Unset = UNSET
    rows: list[AnswerRowsType0Item] | None | Unset = UNSET
    sql_used: None | str | Unset = UNSET
    suggestions: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.answer_rows_type_0_item import AnswerRowsType0Item
        from ..models.provenance import Provenance
        answer = self.answer

        audit_id = self.audit_id

        provenance = self.provenance.to_dict()

        route = self.route

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
        if row_count is not UNSET:
            field_dict["row_count"] = row_count
        if rows is not UNSET:
            field_dict["rows"] = rows
        if sql_used is not UNSET:
            field_dict["sql_used"] = sql_used
        if suggestions is not UNSET:
            field_dict["suggestions"] = suggestions

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.answer_rows_type_0_item import AnswerRowsType0Item
        from ..models.provenance import Provenance
        d = dict(src_dict)
        answer = d.pop("answer")

        audit_id = d.pop("audit_id")

        provenance = Provenance.from_dict(d.pop("provenance"))




        route = d.pop("route")

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
            row_count=row_count,
            rows=rows,
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
