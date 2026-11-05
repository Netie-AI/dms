from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InsightsUsage")



@_attrs_define
class InsightsUsage:
    """ 1.6.0 response field ``usage`` on POST /v1/insights.

    Sum of the counts each FreeRoute complete() call reported. A count is
    null when it was omitted. A missing count is never stored as 0. The
    live crew return carries prompt_tokens and completion_tokens. total_tokens
    is null when that return omits the key. The key itself is omitted when
    schema_context is absent or no count was reported.

        Attributes:
            completion_tokens (int | None | Unset):
            prompt_tokens (int | None | Unset):
            total_tokens (int | None | Unset):
     """

    completion_tokens: int | None | Unset = UNSET
    prompt_tokens: int | None | Unset = UNSET
    total_tokens: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        completion_tokens: int | None | Unset
        if isinstance(self.completion_tokens, Unset):
            completion_tokens = UNSET
        else:
            completion_tokens = self.completion_tokens

        prompt_tokens: int | None | Unset
        if isinstance(self.prompt_tokens, Unset):
            prompt_tokens = UNSET
        else:
            prompt_tokens = self.prompt_tokens

        total_tokens: int | None | Unset
        if isinstance(self.total_tokens, Unset):
            total_tokens = UNSET
        else:
            total_tokens = self.total_tokens


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
        })
        if completion_tokens is not UNSET:
            field_dict["completion_tokens"] = completion_tokens
        if prompt_tokens is not UNSET:
            field_dict["prompt_tokens"] = prompt_tokens
        if total_tokens is not UNSET:
            field_dict["total_tokens"] = total_tokens

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        def _parse_completion_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        completion_tokens = _parse_completion_tokens(d.pop("completion_tokens", UNSET))


        def _parse_prompt_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        prompt_tokens = _parse_prompt_tokens(d.pop("prompt_tokens", UNSET))


        def _parse_total_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        total_tokens = _parse_total_tokens(d.pop("total_tokens", UNSET))


        insights_usage = cls(
            completion_tokens=completion_tokens,
            prompt_tokens=prompt_tokens,
            total_tokens=total_tokens,
        )


        insights_usage.additional_properties = d
        return insights_usage

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
