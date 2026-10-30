from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MemoryRead")



@_attrs_define
class MemoryRead:
    """ 1.4.0: provenance of one per-Space memory entry an answer read.

        Attributes:
            id (str):
            kind (str):
            served_at (str):
            source (str):
            space_id (str):
            version (int):
            written_at (str):
     """

    id: str
    kind: str
    served_at: str
    source: str
    space_id: str
    version: int
    written_at: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        id = self.id

        kind = self.kind

        served_at = self.served_at

        source = self.source

        space_id = self.space_id

        version = self.version

        written_at = self.written_at


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "id": id,
            "kind": kind,
            "served_at": served_at,
            "source": source,
            "space_id": space_id,
            "version": version,
            "written_at": written_at,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        kind = d.pop("kind")

        served_at = d.pop("served_at")

        source = d.pop("source")

        space_id = d.pop("space_id")

        version = d.pop("version")

        written_at = d.pop("written_at")

        memory_read = cls(
            id=id,
            kind=kind,
            served_at=served_at,
            source=source,
            space_id=space_id,
            version=version,
            written_at=written_at,
        )


        memory_read.additional_properties = d
        return memory_read

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
