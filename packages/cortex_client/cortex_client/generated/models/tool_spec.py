from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.tool_class import ToolClass

T = TypeVar("T", bound="ToolSpec")



@_attrs_define
class ToolSpec:
    """ 
        Attributes:
            class_name (ToolClass):
            description (str):
            id (str):
     """

    class_name: ToolClass
    description: str
    id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        class_name = self.class_name.value

        description = self.description

        id = self.id


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "class_name": class_name,
            "description": description,
            "id": id,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        class_name = ToolClass(d.pop("class_name"))




        description = d.pop("description")

        id = d.pop("id")

        tool_spec = cls(
            class_name=class_name,
            description=description,
            id=id,
        )


        tool_spec.additional_properties = d
        return tool_spec

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
