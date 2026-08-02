from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PoolSpec")



@_attrs_define
class PoolSpec:
    """ 
        Attributes:
            id (str):
            class_name (str | Unset):  Default: 'default'.
            max_concurrency (int | Unset):  Default: 1.
     """

    id: str
    class_name: str | Unset = 'default'
    max_concurrency: int | Unset = 1
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        id = self.id

        class_name = self.class_name

        max_concurrency = self.max_concurrency


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "id": id,
        })
        if class_name is not UNSET:
            field_dict["class_name"] = class_name
        if max_concurrency is not UNSET:
            field_dict["max_concurrency"] = max_concurrency

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        class_name = d.pop("class_name", UNSET)

        max_concurrency = d.pop("max_concurrency", UNSET)

        pool_spec = cls(
            id=id,
            class_name=class_name,
            max_concurrency=max_concurrency,
        )


        pool_spec.additional_properties = d
        return pool_spec

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
