from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
  from ..models.ledger_entry_payload import LedgerEntryPayload





T = TypeVar("T", bound="LedgerEntry")



@_attrs_define
class LedgerEntry:
    """ 
        Attributes:
            actor (str):
            created_at (str):
            entry_hash (str):
            event_type (str):
            id (str):
            payload (LedgerEntryPayload):
            prev_hash (str):
            seq (int):
     """

    actor: str
    created_at: str
    entry_hash: str
    event_type: str
    id: str
    payload: LedgerEntryPayload
    prev_hash: str
    seq: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        actor = self.actor

        created_at = self.created_at

        entry_hash = self.entry_hash

        event_type = self.event_type

        id = self.id

        payload = self.payload.to_dict()

        prev_hash = self.prev_hash

        seq = self.seq


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "actor": actor,
            "created_at": created_at,
            "entry_hash": entry_hash,
            "event_type": event_type,
            "id": id,
            "payload": payload,
            "prev_hash": prev_hash,
            "seq": seq,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ledger_entry_payload import LedgerEntryPayload
        d = dict(src_dict)
        actor = d.pop("actor")

        created_at = d.pop("created_at")

        entry_hash = d.pop("entry_hash")

        event_type = d.pop("event_type")

        id = d.pop("id")

        payload = LedgerEntryPayload.from_dict(d.pop("payload"))




        prev_hash = d.pop("prev_hash")

        seq = d.pop("seq")

        ledger_entry = cls(
            actor=actor,
            created_at=created_at,
            entry_hash=entry_hash,
            event_type=event_type,
            id=id,
            payload=payload,
            prev_hash=prev_hash,
            seq=seq,
        )


        ledger_entry.additional_properties = d
        return ledger_entry

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
