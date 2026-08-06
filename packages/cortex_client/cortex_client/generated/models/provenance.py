from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.badge import Badge
from ..types import UNSET, Unset

T = TypeVar("T", bound="Provenance")



@_attrs_define
class Provenance:
    """ 
        Attributes:
            badge (Badge):
            layer (str):
            assumptions (None | str | Unset):
            metric_id (None | str | Unset):
            query_source (None | str | Unset):
     """

    badge: Badge
    layer: str
    assumptions: None | str | Unset = UNSET
    metric_id: None | str | Unset = UNSET
    query_source: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        badge = self.badge.value

        layer = self.layer

        assumptions: None | str | Unset
        if isinstance(self.assumptions, Unset):
            assumptions = UNSET
        else:
            assumptions = self.assumptions

        metric_id: None | str | Unset
        if isinstance(self.metric_id, Unset):
            metric_id = UNSET
        else:
            metric_id = self.metric_id

        query_source: None | str | Unset
        if isinstance(self.query_source, Unset):
            query_source = UNSET
        else:
            query_source = self.query_source


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "badge": badge,
            "layer": layer,
        })
        if assumptions is not UNSET:
            field_dict["assumptions"] = assumptions
        if metric_id is not UNSET:
            field_dict["metric_id"] = metric_id
        if query_source is not UNSET:
            field_dict["query_source"] = query_source

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        badge = Badge(d.pop("badge"))




        layer = d.pop("layer")

        def _parse_assumptions(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        assumptions = _parse_assumptions(d.pop("assumptions", UNSET))


        def _parse_metric_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        metric_id = _parse_metric_id(d.pop("metric_id", UNSET))


        def _parse_query_source(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        query_source = _parse_query_source(d.pop("query_source", UNSET))


        provenance = cls(
            badge=badge,
            layer=layer,
            assumptions=assumptions,
            metric_id=metric_id,
            query_source=query_source,
        )


        provenance.additional_properties = d
        return provenance

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
