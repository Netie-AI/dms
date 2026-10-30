from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
  from ..models.insights_usage import InsightsUsage





T = TypeVar("T", bound="InsightsAskResponse200")



@_attrs_define
class InsightsAskResponse200:
    """ 
        Attributes:
            usage (InsightsUsage | Unset): 1.6.0 response field ``usage`` on POST /v1/insights.

                Sum of the counts each FreeRoute complete() call reported. A count is
                null when it was omitted. A missing count is never stored as 0. The
                live crew return carries prompt_tokens and completion_tokens. total_tokens
                is null when that return omits the key. The key itself is omitted when
                schema_context is absent or no count was reported.
     """

    usage: InsightsUsage | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        usage: dict[str, Any] | Unset = UNSET
        if not isinstance(self.usage, Unset):
            usage = self.usage.to_dict()


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
        })
        if usage is not UNSET:
            field_dict["usage"] = usage

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.insights_usage import InsightsUsage
        d = dict(src_dict)
        _usage = d.pop("usage", UNSET)
        usage: InsightsUsage | Unset
        if isinstance(_usage,  Unset):
            usage = UNSET
        else:
            usage = InsightsUsage.from_dict(_usage)




        insights_ask_response_200 = cls(
            usage=usage,
        )


        insights_ask_response_200.additional_properties = d
        return insights_ask_response_200

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
