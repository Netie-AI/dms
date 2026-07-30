from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="ChainVerification")



@_attrs_define
class ChainVerification:
    """ 
        Attributes:
            ok (bool):
            broken_at (int | None | Unset):
     """

    ok: bool
    broken_at: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        ok = self.ok

        broken_at: int | None | Unset
        if isinstance(self.broken_at, Unset):
            broken_at = UNSET
        else:
            broken_at = self.broken_at


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "ok": ok,
        })
        if broken_at is not UNSET:
            field_dict["broken_at"] = broken_at

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        ok = d.pop("ok")

        def _parse_broken_at(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        broken_at = _parse_broken_at(d.pop("broken_at", UNSET))


        chain_verification = cls(
            ok=ok,
            broken_at=broken_at,
        )


        chain_verification.additional_properties = d
        return chain_verification

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
