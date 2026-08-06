from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ContributingSource")



@_attrs_define
class ContributingSource:
    """ One source card for the Sources panel (architecture §4.7 / §4.8).

        Attributes:
            ref_id (str):
            container (None | str | Unset):
            contribution (float | None | Unset):
            kind (None | str | Unset):
            member (None | str | Unset):
            row_count (int | None | Unset):
     """

    ref_id: str
    container: None | str | Unset = UNSET
    contribution: float | None | Unset = UNSET
    kind: None | str | Unset = UNSET
    member: None | str | Unset = UNSET
    row_count: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        ref_id = self.ref_id

        container: None | str | Unset
        if isinstance(self.container, Unset):
            container = UNSET
        else:
            container = self.container

        contribution: float | None | Unset
        if isinstance(self.contribution, Unset):
            contribution = UNSET
        else:
            contribution = self.contribution

        kind: None | str | Unset
        if isinstance(self.kind, Unset):
            kind = UNSET
        else:
            kind = self.kind

        member: None | str | Unset
        if isinstance(self.member, Unset):
            member = UNSET
        else:
            member = self.member

        row_count: int | None | Unset
        if isinstance(self.row_count, Unset):
            row_count = UNSET
        else:
            row_count = self.row_count


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "ref_id": ref_id,
        })
        if container is not UNSET:
            field_dict["container"] = container
        if contribution is not UNSET:
            field_dict["contribution"] = contribution
        if kind is not UNSET:
            field_dict["kind"] = kind
        if member is not UNSET:
            field_dict["member"] = member
        if row_count is not UNSET:
            field_dict["row_count"] = row_count

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        ref_id = d.pop("ref_id")

        def _parse_container(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        container = _parse_container(d.pop("container", UNSET))


        def _parse_contribution(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        contribution = _parse_contribution(d.pop("contribution", UNSET))


        def _parse_kind(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        kind = _parse_kind(d.pop("kind", UNSET))


        def _parse_member(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        member = _parse_member(d.pop("member", UNSET))


        def _parse_row_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        row_count = _parse_row_count(d.pop("row_count", UNSET))


        contributing_source = cls(
            ref_id=ref_id,
            container=container,
            contribution=contribution,
            kind=kind,
            member=member,
            row_count=row_count,
        )


        contributing_source.additional_properties = d
        return contributing_source

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
