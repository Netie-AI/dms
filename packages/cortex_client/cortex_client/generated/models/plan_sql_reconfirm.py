from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PlanSqlReconfirm")



@_attrs_define
class PlanSqlReconfirm:
    """ 1.5.0: why the plan+SQL loop would abstain, and the closest question it can run.

    Present only when the loop could not ground an answer. Yes
    (``AskRequest.plan_sql_confirm`` = ``yes``, with ``question`` set to
    ``closest_question``) runs that question. No answers
    ``not found in the database``. Older clients ignore the object.

        Attributes:
            closest_question (str):
            why (str):
     """

    closest_question: str
    why: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        closest_question = self.closest_question

        why = self.why


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "closest_question": closest_question,
            "why": why,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        closest_question = d.pop("closest_question")

        why = d.pop("why")

        plan_sql_reconfirm = cls(
            closest_question=closest_question,
            why=why,
        )


        plan_sql_reconfirm.additional_properties = d
        return plan_sql_reconfirm

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
