from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
  from ..models.ledger_append_request_payload import LedgerAppendRequestPayload





T = TypeVar("T", bound="LedgerAppendRequest")



@_attrs_define
class LedgerAppendRequest:
    """ 
        Attributes:
            actor (str):
            event_type (str):
            payload (LedgerAppendRequestPayload | Unset):
     """

    actor: str
    event_type: str
    payload: LedgerAppendRequestPayload | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        actor = self.actor

        event_type = self.event_type

        payload: dict[str, Any] | Unset = UNSET
        if not isinstance(self.payload, Unset):
            payload = self.payload.to_dict()


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "actor": actor,
            "event_type": event_type,
        })
        if payload is not UNSET:
            field_dict["payload"] = payload

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ledger_append_request_payload import LedgerAppendRequestPayload
        d = dict(src_dict)
        actor = d.pop("actor")

        event_type = d.pop("event_type")

        _payload = d.pop("payload", UNSET)
        payload: LedgerAppendRequestPayload | Unset
        if isinstance(_payload,  Unset):
            payload = UNSET
        else:
            payload = LedgerAppendRequestPayload.from_dict(_payload)




        ledger_append_request = cls(
            actor=actor,
            event_type=event_type,
            payload=payload,
        )


        ledger_append_request.additional_properties = d
        return ledger_append_request

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
