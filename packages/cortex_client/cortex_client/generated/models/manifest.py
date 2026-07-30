from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast

if TYPE_CHECKING:
  from ..models.manifest_row_predicates import ManifestRowPredicates





T = TypeVar("T", bound="Manifest")



@_attrs_define
class Manifest:
    """ A signed grant of what one session may read.

    DMS mints and signs; Cortex enforces; OpenVault roots the key. Every field
    added in 1.1.0 is optional on the wire so a 1.0.0 producer still validates —
    the requirement that a manifest carry an issuer, an issue time and a pool is
    enforced by the verifier (``CortexOS.execution.manifest``), which rejects
    what it cannot check. A permissive type with a strict gate keeps DMS pinned
    to contract major 1 while the engine refuses anything unverifiable.

        Attributes:
            expires_at (str):
            org_id (str):
            session_id (str):
            signature (str):
            allowed_paths (list[str] | Unset):
            issued_at (None | str | Unset):
            issuer_key_id (None | str | Unset):
            pool_id (None | str | Unset):
            row_predicate_sql (None | str | Unset):
            row_predicates (ManifestRowPredicates | Unset): Table name -> SQL boolean expression, injected per referenced
                table.
            space_id (None | str | Unset):
     """

    expires_at: str
    org_id: str
    session_id: str
    signature: str
    allowed_paths: list[str] | Unset = UNSET
    issued_at: None | str | Unset = UNSET
    issuer_key_id: None | str | Unset = UNSET
    pool_id: None | str | Unset = UNSET
    row_predicate_sql: None | str | Unset = UNSET
    row_predicates: ManifestRowPredicates | Unset = UNSET
    space_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.manifest_row_predicates import ManifestRowPredicates
        expires_at = self.expires_at

        org_id = self.org_id

        session_id = self.session_id

        signature = self.signature

        allowed_paths: list[str] | Unset = UNSET
        if not isinstance(self.allowed_paths, Unset):
            allowed_paths = self.allowed_paths



        issued_at: None | str | Unset
        if isinstance(self.issued_at, Unset):
            issued_at = UNSET
        else:
            issued_at = self.issued_at

        issuer_key_id: None | str | Unset
        if isinstance(self.issuer_key_id, Unset):
            issuer_key_id = UNSET
        else:
            issuer_key_id = self.issuer_key_id

        pool_id: None | str | Unset
        if isinstance(self.pool_id, Unset):
            pool_id = UNSET
        else:
            pool_id = self.pool_id

        row_predicate_sql: None | str | Unset
        if isinstance(self.row_predicate_sql, Unset):
            row_predicate_sql = UNSET
        else:
            row_predicate_sql = self.row_predicate_sql

        row_predicates: dict[str, Any] | Unset = UNSET
        if not isinstance(self.row_predicates, Unset):
            row_predicates = self.row_predicates.to_dict()

        space_id: None | str | Unset
        if isinstance(self.space_id, Unset):
            space_id = UNSET
        else:
            space_id = self.space_id


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "expires_at": expires_at,
            "org_id": org_id,
            "session_id": session_id,
            "signature": signature,
        })
        if allowed_paths is not UNSET:
            field_dict["allowed_paths"] = allowed_paths
        if issued_at is not UNSET:
            field_dict["issued_at"] = issued_at
        if issuer_key_id is not UNSET:
            field_dict["issuer_key_id"] = issuer_key_id
        if pool_id is not UNSET:
            field_dict["pool_id"] = pool_id
        if row_predicate_sql is not UNSET:
            field_dict["row_predicate_sql"] = row_predicate_sql
        if row_predicates is not UNSET:
            field_dict["row_predicates"] = row_predicates
        if space_id is not UNSET:
            field_dict["space_id"] = space_id

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.manifest_row_predicates import ManifestRowPredicates
        d = dict(src_dict)
        expires_at = d.pop("expires_at")

        org_id = d.pop("org_id")

        session_id = d.pop("session_id")

        signature = d.pop("signature")

        allowed_paths = cast(list[str], d.pop("allowed_paths", UNSET))


        def _parse_issued_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        issued_at = _parse_issued_at(d.pop("issued_at", UNSET))


        def _parse_issuer_key_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        issuer_key_id = _parse_issuer_key_id(d.pop("issuer_key_id", UNSET))


        def _parse_pool_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pool_id = _parse_pool_id(d.pop("pool_id", UNSET))


        def _parse_row_predicate_sql(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        row_predicate_sql = _parse_row_predicate_sql(d.pop("row_predicate_sql", UNSET))


        _row_predicates = d.pop("row_predicates", UNSET)
        row_predicates: ManifestRowPredicates | Unset
        if isinstance(_row_predicates,  Unset):
            row_predicates = UNSET
        else:
            row_predicates = ManifestRowPredicates.from_dict(_row_predicates)




        def _parse_space_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        space_id = _parse_space_id(d.pop("space_id", UNSET))


        manifest = cls(
            expires_at=expires_at,
            org_id=org_id,
            session_id=session_id,
            signature=signature,
            allowed_paths=allowed_paths,
            issued_at=issued_at,
            issuer_key_id=issuer_key_id,
            pool_id=pool_id,
            row_predicate_sql=row_predicate_sql,
            row_predicates=row_predicates,
            space_id=space_id,
        )


        manifest.additional_properties = d
        return manifest

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
