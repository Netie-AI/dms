from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
  from ..models.manifest import Manifest
  from ..models.pool_spec import PoolSpec
  from ..models.submit_request_body import SubmitRequestBody
  from ..models.submit_request_plan import SubmitRequestPlan





T = TypeVar("T", bound="SubmitRequest")



@_attrs_define
class SubmitRequest:
    """ 
        Attributes:
            body (SubmitRequestBody):
            manifest (Manifest): A signed grant of what one session may read.

                DMS mints and signs; Cortex enforces; OpenVault roots the key. Every field
                added in 1.1.0 is optional on the wire so a 1.0.0 producer still validates —
                the requirement that a manifest carry an issuer, an issue time and a pool is
                enforced by the verifier (``CortexOS.execution.manifest``), which rejects
                what it cannot check. A permissive type with a strict gate keeps DMS pinned
                to contract major 1 while the engine refuses anything unverifiable.
            plan (SubmitRequestPlan):
            pool (PoolSpec):
     """

    body: SubmitRequestBody
    manifest: Manifest
    plan: SubmitRequestPlan
    pool: PoolSpec
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        body = self.body.to_dict()

        manifest = self.manifest.to_dict()

        plan = self.plan.to_dict()

        pool = self.pool.to_dict()


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "body": body,
            "manifest": manifest,
            "plan": plan,
            "pool": pool,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.manifest import Manifest
        from ..models.pool_spec import PoolSpec
        from ..models.submit_request_body import SubmitRequestBody
        from ..models.submit_request_plan import SubmitRequestPlan
        d = dict(src_dict)
        body = SubmitRequestBody.from_dict(d.pop("body"))




        manifest = Manifest.from_dict(d.pop("manifest"))




        plan = SubmitRequestPlan.from_dict(d.pop("plan"))




        pool = PoolSpec.from_dict(d.pop("pool"))




        submit_request = cls(
            body=body,
            manifest=manifest,
            plan=plan,
            pool=pool,
        )


        submit_request.additional_properties = d
        return submit_request

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
