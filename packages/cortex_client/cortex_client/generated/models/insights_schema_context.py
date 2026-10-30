from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InsightsSchemaContext")



@_attrs_define
class InsightsSchemaContext:
    """ 1.6.0 request field on POST /v1/insights.

    The engine reads this on ``InsightsWireIn.schema_context``, not on
    ``InsightsAskIn``. One optional string. Present: it replaces the pack
    table list. Absent: the pack list stays, and the response stays
    byte-equal to the 279cbd85 pin. The ``usage`` key is omitted when
    ``schema_context`` is absent or no count was reported.

    Shortlist pick reasons are not a sibling field. The caller writes them
    into this string: a table line carries ``reason=score:<score>``, a
    column line may carry ``reason=<why>``, and a join line carries
    ``reason=<why>``.

        Attributes:
            schema_context (None | str | Unset): Caller shortlist. Replaces the pack table list when present. Pick reasons
                live in this string, not in a separate field: table lines use reason=score:<score>, column lines may include
                reason=<why>, join lines include reason=<why>. maxLength 8192. The engine cap is 8192 UTF-8 bytes.
     """

    schema_context: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        schema_context: None | str | Unset
        if isinstance(self.schema_context, Unset):
            schema_context = UNSET
        else:
            schema_context = self.schema_context


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
        })
        if schema_context is not UNSET:
            field_dict["schema_context"] = schema_context

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        def _parse_schema_context(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        schema_context = _parse_schema_context(d.pop("schema_context", UNSET))


        insights_schema_context = cls(
            schema_context=schema_context,
        )


        insights_schema_context.additional_properties = d
        return insights_schema_context

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
