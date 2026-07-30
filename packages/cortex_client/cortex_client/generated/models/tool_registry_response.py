from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import cast

if TYPE_CHECKING:
  from ..models.tool_spec import ToolSpec





T = TypeVar("T", bound="ToolRegistryResponse")



@_attrs_define
class ToolRegistryResponse:
    """ 
        Attributes:
            tools (list[ToolSpec]):
     """

    tools: list[ToolSpec]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.tool_spec import ToolSpec
        tools = []
        for tools_item_data in self.tools:
            tools_item = tools_item_data.to_dict()
            tools.append(tools_item)




        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "tools": tools,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.tool_spec import ToolSpec
        d = dict(src_dict)
        tools = []
        _tools = d.pop("tools")
        for tools_item_data in (_tools):
            tools_item = ToolSpec.from_dict(tools_item_data)



            tools.append(tools_item)


        tool_registry_response = cls(
            tools=tools,
        )


        tool_registry_response.additional_properties = d
        return tool_registry_response

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
