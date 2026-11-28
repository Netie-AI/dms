"""Envelope, signers, PAdES sign, verify, disclose.

The model may draft text and pass filled fields. ``sign`` refuses a model
actor. The private key is loaded for the sign call and not stored.

ponytail: ``_PROCESS_LOCK`` serializes signs in this process, including the
optional Cortex append. Ceiling: one sign at a time per process. Upgrade: a
document-row lock in Postgres and the Cortex id on ``dms.ledger_ref``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from dms_core.signing.chain import GENESIS, intact, link
from dms_core.signing.errors import SignError
from dms_core.signing.flag import enabled
from dms_core.signing.material import SigningMaterial, cert_fingerprint
from dms_core.signing.pdfsign import (
    INTENT,
    assert_pdf,
    render_pdf,
    sha256_hex,
    sign_pdf,
    timestamper_for,
    verify_pdf,
    watermark_pdf,
)
from dms_core.signing.store import DEFAULT_TENANT

_PROCESS_LOCK = threading.RLock()
_MODEL_ACTORS = frozenset({"model", "assistant", "ai"})
_PNG_MAX = 512_000
Ledger = Callable[[str, dict[str, Any], str], str | None]
KeyLoader = Callable[[str], SigningMaterial]
Clock = Callable[[], datetime]


class Actor:
    def __init__(self, identity: str, ip: str = "", user_agent: str = "") -> None:
        self.identity = identity
        self.ip = ip
        self.user_agent = user_agent


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(text: str) -> datetime:
    raw = text[:-1] if text.endswith("Z") else text
    if "." not in raw:
        raw = raw + ".000000"
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require() -> None:
    if not enabled():
        raise SignError("sign_disabled", 404)


def _png(raw_b64: str) -> bytes:
    try:
        data = base64.b64decode(raw_b64, validate=True)
    except Exception:
        raise SignError("capture_invalid") from None
    if len(data) > _PNG_MAX or not data.startswith(b"\x89PNG"):
        raise SignError("capture_invalid")
    return data


class SignService:
    def __init__(
        self,
        store: Any,
        *,
        tenant_id: str = DEFAULT_TENANT,
        keys: KeyLoader,
        objects: Any | None = None,
        ledger: Ledger | None = None,
        tsa_url: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.tenant = tenant_id
        self.keys = keys
        self.objects = objects
        self.ledger = ledger
        self.tsa_url = os.environ.get("DMS_SIGN_TSA_URL", "") if tsa_url is None else tsa_url
        self._clock = clock
        self._tick = 0

    def _now(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        return datetime.now(UTC)

    def _stamp(self) -> str:
        """Unique audit time. A frozen clock still moves forward by one microsecond."""
        self._tick += 1
        return _iso(self._now() + timedelta(microseconds=self._tick))

    def _put(self, document_id: str, version: int, data: bytes) -> dict[str, Any]:
        digest = sha256_hex(data)
        if self.objects is not None:
            key = f"sign/{self.tenant}/{document_id}/v{version}"
            self.objects.put(key, data)
            return {"sha256": digest, "blob": None, "object_key": key, "storage": "object_store"}
        return {"sha256": digest, "blob": data, "object_key": "", "storage": "db"}

    def _read(self, doc: dict[str, Any]) -> bytes:
        if doc.get("storage") == "object_store":
            if self.objects is None:
                raise SignError("sign_store_failed", 503)
            return self.objects.get(str(doc["object_key"]))
        blob = doc.get("blob")
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            raise SignError("sign_store_failed", 503)
        return bytes(blob)

    def _audit(self, **fields: Any) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": str(uuid4()),
            "tenant_id": fields.get("tenant_id") or self.tenant,
            "document_id": fields["document_id"],
            "envelope_id": fields.get("envelope_id") or "",
            "event_type": fields["event_type"],
            "identity": fields.get("identity") or "",
            "auth_method": fields.get("auth_method") or "",
            "ip": str(fields.get("ip") or "")[:64],
            "user_agent": str(fields.get("user_agent") or "")[:300],
            "consent_text": str(fields.get("consent_text") or "")[:2000],
            "intent": str(fields.get("intent") or ""),
            "sha256_before": fields.get("sha256_before") or "",
            "sha256_after": fields.get("sha256_after") or "",
            "created_at": fields.get("created_at") or self._stamp(),
            "cortex_entry_id": "",
        }
        events = self.store.audits_for(row["tenant_id"], row["document_id"])
        prev = events[-1]["event_hash"] if events else GENESIS
        row["prev_hash"] = prev
        row["event_hash"] = link(prev, row)
        if self.ledger is not None:
            try:
                entry = self.ledger(
                    str(row["event_type"]),
                    {
                        "document_id": row["document_id"],
                        "event_type": row["event_type"],
                        "sha256_before": row["sha256_before"],
                        "sha256_after": row["sha256_after"],
                        "identity": row["identity"],
                        "event_hash": row["event_hash"],
                    },
                    str(row["identity"]),
                )
            except Exception:
                entry = None
            if isinstance(entry, str) and entry:
                row["cortex_entry_id"] = entry[:80]
        return self.store.append_prepared(row)

    def _fields(self, raw: dict[str, str] | None) -> dict[str, str]:
        if not raw:
            return {}
        out: dict[str, str] = {}
        for key, value in raw.items():
            name = str(key).strip()
            if not name or len(name) > 80:
                raise SignError("bad_request")
            out[name] = str(value)[:500]
            if len(out) > 40:
                raise SignError("bad_request")
        return out

    def draft(
        self,
        *,
        title: str,
        body: str,
        fields: dict[str, str] | None,
        actor: Actor,
        pdf: bytes | None = None,
    ) -> dict[str, Any]:
        """Unsigned document. The model may call this. It does not sign."""
        _require()
        return self._save_new(
            title=title,
            body=body,
            fields=self._fields(fields),
            pdf=pdf,
            actor=actor,
            envelope=False,
            order="parallel",
        )

    def create_envelope(
        self,
        *,
        title: str,
        body: str,
        fields: dict[str, str] | None,
        order: str,
        actor: Actor,
        pdf: bytes | None = None,
    ) -> dict[str, Any]:
        _require()
        if order not in {"sequential", "parallel"}:
            raise SignError("bad_request")
        return self._save_new(
            title=title,
            body=body,
            fields=self._fields(fields),
            pdf=pdf,
            actor=actor,
            envelope=True,
            order=order,
        )

    def _save_new(
        self,
        *,
        title: str,
        body: str,
        fields: dict[str, str],
        pdf: bytes | None,
        actor: Actor,
        envelope: bool,
        order: str,
    ) -> dict[str, Any]:
        title = title.strip()
        if not title or len(title) > 200:
            raise SignError("bad_request")
        if len(body) > 8000:
            raise SignError("bad_request")
        if pdf:
            assert_pdf(pdf)
            data = pdf
        else:
            data = render_pdf(title, body, fields)
        now = _iso(self._now())
        document_id = str(uuid4())
        placed = self._put(document_id, 1, data)
        doc = {
            "id": document_id,
            "tenant_id": self.tenant,
            "envelope_id": "",
            "title": title,
            "body": body,
            "fields_json": json.dumps(fields, sort_keys=True),
            "sha256": placed["sha256"],
            "blob": placed["blob"],
            "object_key": placed["object_key"],
            "storage": placed["storage"],
            "version": 1,
            "created_at": now,
        }
        envelope_id = ""
        status = "draft"
        with _PROCESS_LOCK:
            self.store.save_document(doc)
            self.store.add_version(
                {
                    "id": str(uuid4()),
                    "tenant_id": self.tenant,
                    "document_id": document_id,
                    "version": 1,
                    "sha256": placed["sha256"],
                    "blob": placed["blob"],
                    "object_key": placed["object_key"],
                    "storage": placed["storage"],
                    "created_at": now,
                }
            )
            if envelope:
                envelope_id = str(uuid4())
                status = "pending"
                doc["envelope_id"] = envelope_id
                self.store.save_document(doc)
                self.store.save_envelope(
                    {
                        "id": envelope_id,
                        "tenant_id": self.tenant,
                        "document_id": document_id,
                        "order_mode": order,
                        "status": status,
                        "created_at": now,
                    }
                )
            self._audit(
                document_id=document_id,
                envelope_id=envelope_id,
                event_type="document_created",
                identity=actor.identity,
                auth_method="steward",
                ip=actor.ip,
                user_agent=actor.user_agent,
                sha256_before=placed["sha256"],
                sha256_after=placed["sha256"],
            )
        return {
            "document_id": document_id,
            "envelope_id": envelope_id,
            "sha256": placed["sha256"],
            "status": status,
            "signatures": [],
        }

    def add_signer(
        self,
        envelope_id: str,
        *,
        email: str,
        auth_method: str,
        identity: str,
        position: int,
        ttl_s: int,
        actor: Actor,
    ) -> dict[str, Any]:
        _require()
        if auth_method not in {"sso", "email_otp"}:
            raise SignError("bad_request")
        email = email.strip().lower()
        if "@" not in email or len(email) > 200:
            raise SignError("bad_request")
        if position < 1 or position > 20:
            raise SignError("bad_request")
        if ttl_s < 60 or ttl_s > 30 * 24 * 3600:
            raise SignError("bad_request")
        with _PROCESS_LOCK:
            env = self.store.get_envelope(self.tenant, envelope_id)
            if env is None:
                raise SignError("envelope_unknown", 404)
            existing = self.store.signers_for(self.tenant, envelope_id)
            if any(int(row["position"]) == position for row in existing):
                raise SignError("bad_request")
            token = ""
            otp = ""
            if auth_method == "sso":
                who = identity.strip()
                if not who or len(who) > 200:
                    raise SignError("bad_request")
            else:
                who = email
                token = secrets.token_urlsafe(32)
                otp = f"{secrets.randbelow(1_000_000):06d}"
            now = self._now()
            signer_id = str(uuid4())
            row = {
                "id": signer_id,
                "tenant_id": self.tenant,
                "envelope_id": envelope_id,
                "position": position,
                "email": email,
                "auth_method": auth_method,
                "identity": who,
                "status": "pending",
                "token_hash": _digest(token) if token else "",
                "otp_hash": _digest(otp) if otp else "",
                "expires_at": _iso(now + timedelta(seconds=ttl_s)),
                "consent_text": "",
                "intent": "",
                "signed_at": None,
                "created_at": _iso(now),
            }
            self.store.save_signer(row)
            self._audit(
                document_id=env["document_id"],
                envelope_id=envelope_id,
                event_type="signer_added",
                identity=actor.identity,
                auth_method="steward",
                ip=actor.ip,
                user_agent=actor.user_agent,
                sha256_before="",
                sha256_after="",
            )
        out: dict[str, Any] = {
            "signer_id": signer_id,
            "auth_method": auth_method,
            "expires_at": row["expires_at"],
        }
        # Returned once so a steward can deliver them. A mailer is the swap.
        # Callers must not log this dict.
        if token:
            out["token"] = token
            out["otp"] = otp
        return out

    def _capture(
        self, capture: dict[str, Any], signer: dict[str, Any]
    ) -> tuple[str, bytes | None, str, str]:
        if capture.get("by_model") is True:
            raise SignError("model_cannot_sign", 403)
        kind = capture.get("kind")
        if kind == "typed":
            text = str(capture.get("text") or "").strip()
            if not text or len(text) > 80:
                raise SignError("capture_invalid")
            return "typed", None, "", text
        if kind in {"drawn", "upload"}:
            png = _png(str(capture.get("png_b64") or ""))
            svg = str(capture.get("svg") or "") if kind == "drawn" else ""
            if len(svg) > 100_000:
                raise SignError("capture_invalid")
            return str(kind), png, svg, str(signer["email"])
        raise SignError("capture_invalid")

    def sign(
        self,
        envelope_id: str,
        *,
        signer_id: str,
        token: str,
        otp: str,
        consent_text: str,
        intent: str,
        capture: dict[str, Any],
        actor: Actor,
        timestamper: Any | None = None,
    ) -> dict[str, Any]:
        _require()
        if actor.identity.strip().lower() in _MODEL_ACTORS:
            raise SignError("model_cannot_sign", 403)
        if intent != INTENT:
            raise SignError("intent_required")
        if not consent_text.strip() or INTENT not in consent_text:
            raise SignError("consent_required")
        with _PROCESS_LOCK:
            env = self.store.get_envelope(self.tenant, envelope_id)
            if env is None:
                raise SignError("envelope_unknown", 404)
            signer = self.store.get_signer(self.tenant, signer_id)
            if signer is None or signer["envelope_id"] != envelope_id:
                raise SignError("signer_unknown", 404)
            if signer["status"] != "pending":
                raise SignError("link_consumed")
            if self._now() > _parse(str(signer["expires_at"])):
                raise SignError("link_expired")
            if signer["auth_method"] == "email_otp":
                if not token or not hmac.compare_digest(_digest(token), str(signer["token_hash"])):
                    raise SignError("token_invalid")
                if not otp or not hmac.compare_digest(_digest(otp), str(signer["otp_hash"])):
                    raise SignError("otp_invalid")
                who = str(signer["email"])
            else:
                if actor.identity != signer["identity"]:
                    raise SignError("signer_unknown", 403)
                who = str(signer["identity"])
            if env["order_mode"] == "sequential":
                for other in self.store.signers_for(self.tenant, envelope_id):
                    earlier = int(other["position"]) < int(signer["position"])
                    if earlier and other["status"] != "signed":
                        raise SignError("out_of_order")
            kind, png, svg, stamp_name = self._capture(capture, signer)
            doc = self.store.get_document(self.tenant, env["document_id"])
            if doc is None:
                raise SignError("document_unknown", 404)
            current = self._read(doc)
            before = sha256_hex(current)
            material = self.keys(self.tenant)
            cert_pem = material.cert_pem.decode("ascii")
            fingerprint = cert_fingerprint(material.cert_pem)
            ts = timestamper if timestamper is not None else timestamper_for(self.tsa_url)
            box_y = 48 + (int(signer["position"]) - 1) * 78
            signed = sign_pdf(
                current,
                material=material,
                field_name=f"Sig{int(signer['position'])}",
                signer_name=stamp_name,
                reason=INTENT,
                png=png,
                box=(72, box_y, 320, box_y + 70),
                timestamper=ts,
            )
            del material
            after = sha256_hex(signed)
            version = int(doc["version"]) + 1
            placed = self._put(str(doc["id"]), version, signed)
            now = _iso(self._now())
            doc["sha256"] = placed["sha256"]
            doc["blob"] = placed["blob"]
            doc["object_key"] = placed["object_key"]
            doc["storage"] = placed["storage"]
            doc["version"] = version
            self.store.save_document(doc)
            self.store.add_version(
                {
                    "id": str(uuid4()),
                    "tenant_id": self.tenant,
                    "document_id": doc["id"],
                    "version": version,
                    "sha256": after,
                    "blob": placed["blob"],
                    "object_key": placed["object_key"],
                    "storage": placed["storage"],
                    "created_at": now,
                }
            )
            signer["status"] = "signed"
            signer["token_hash"] = ""
            signer["otp_hash"] = ""
            signer["consent_text"] = consent_text[:2000]
            signer["intent"] = intent
            signer["signed_at"] = now
            self.store.save_signer(signer)
            self.store.add_signature(
                {
                    "id": str(uuid4()),
                    "tenant_id": self.tenant,
                    "document_id": doc["id"],
                    "signer_id": signer["id"],
                    "sha256_before": before,
                    "sha256_after": after,
                    "cert_pem": cert_pem,
                    "cert_sha256": fingerprint,
                    "capture_kind": kind,
                    "capture_svg": svg,
                    "tsa_present": ts is not None,
                    "signed_at": now,
                }
            )
            signers = self.store.signers_for(self.tenant, envelope_id)
            if signers and all(item["status"] == "signed" for item in signers):
                env["status"] = "completed"
            else:
                env["status"] = "partial"
            self.store.save_envelope(env)
            self._audit(
                document_id=doc["id"],
                envelope_id=envelope_id,
                event_type="signed",
                identity=who,
                auth_method=str(signer["auth_method"]),
                ip=actor.ip,
                user_agent=actor.user_agent,
                consent_text=consent_text[:2000],
                intent=intent,
                sha256_before=before,
                sha256_after=after,
            )
        return {
            "document_id": doc["id"],
            "envelope_id": envelope_id,
            "sha256_before": before,
            "sha256_after": after,
            "status": env["status"],
            "tsa": ts is not None,
            "signer_id": signer_id,
        }

    def verify(self, pdf: bytes) -> dict[str, Any]:
        _require()
        assert_pdf(pdf)
        digest = sha256_hex(pdf)
        found = self.store.find_sha(self.tenant, digest)
        document_id = None if found is None else str(found["document_id"])
        pems = self.store.trust_pems(self.tenant, document_id)
        if not pems:
            pems = self.store.trust_pems(self.tenant, None)
        return {
            "sha256": digest,
            "hash_match": found is not None,
            "document_id": document_id,
            "signatures": verify_pdf(pdf, trust_pems=pems),
        }

    def document_file(self, document_id: str, actor: Actor) -> bytes:
        _require()
        with _PROCESS_LOCK:
            doc = self.store.get_document(self.tenant, document_id)
            if doc is None:
                raise SignError("document_unknown", 404)
            data = self._read(doc)
            digest = sha256_hex(data)
            self._audit(
                document_id=document_id,
                envelope_id=str(doc.get("envelope_id") or ""),
                event_type="viewed",
                identity=actor.identity,
                auth_method="steward",
                ip=actor.ip,
                user_agent=actor.user_agent,
                sha256_before=digest,
                sha256_after=digest,
            )
        return data

    def audit(self, document_id: str) -> dict[str, Any]:
        _require()
        doc = self.store.get_document(self.tenant, document_id)
        if doc is None:
            raise SignError("document_unknown", 404)
        rows = self.store.audits_for(self.tenant, document_id)
        events = []
        for row in rows:
            events.append(
                {
                    "id": row["id"],
                    "event_type": row["event_type"],
                    "identity": row["identity"],
                    "auth_method": row["auth_method"],
                    "ip": row["ip"],
                    "user_agent": row["user_agent"],
                    "consent_text": row["consent_text"],
                    "intent": row["intent"],
                    "sha256_before": row["sha256_before"],
                    "sha256_after": row["sha256_after"],
                    "prev_hash": row["prev_hash"],
                    "event_hash": row["event_hash"],
                    "cortex_entry_id": row.get("cortex_entry_id") or "",
                    "created_at": row["created_at"],
                }
            )
        return {"document_id": document_id, "chain_ok": intact(rows), "events": events}

    def disclose(
        self,
        document_id: str,
        *,
        mode: str,
        watermark: bool,
        redact_fields: list[str],
        ttl_s: int,
        actor: Actor,
    ) -> dict[str, Any]:
        _require()
        if mode not in {"view", "download"}:
            raise SignError("bad_request")
        if ttl_s < 60 or ttl_s > 30 * 24 * 3600:
            raise SignError("bad_request")
        if mode == "view":
            watermark = True
        names = []
        for name in redact_fields:
            text = str(name).strip()
            if text:
                names.append(text[:80])
        if len(names) > 40:
            raise SignError("bad_request")
        with _PROCESS_LOCK:
            doc = self.store.get_document(self.tenant, document_id)
            if doc is None:
                raise SignError("document_unknown", 404)
            now = self._now()
            token = secrets.token_urlsafe(32)
            disclosure_id = str(uuid4())
            row = {
                "id": disclosure_id,
                "tenant_id": self.tenant,
                "document_id": document_id,
                "token_hash": _digest(token),
                "mode": mode,
                "watermark": watermark,
                "redact_json": json.dumps(names),
                "expires_at": _iso(now + timedelta(seconds=ttl_s)),
                "revoked_at": None,
                "created_at": _iso(now),
            }
            self.store.save_disclosure(row)
            self._audit(
                document_id=document_id,
                envelope_id=str(doc.get("envelope_id") or ""),
                event_type="disclosed",
                identity=actor.identity,
                auth_method="steward",
                ip=actor.ip,
                user_agent=actor.user_agent,
                sha256_before=str(doc["sha256"]),
                sha256_after=str(doc["sha256"]),
            )
        return {
            "disclosure_id": disclosure_id,
            "token": token,
            "expires_at": row["expires_at"],
            "mode": mode,
            "path": f"/v1/sign/share/{token}",
        }

    def revoke(self, disclosure_id: str, actor: Actor) -> dict[str, Any]:
        _require()
        with _PROCESS_LOCK:
            row = self.store.get_disclosure(self.tenant, disclosure_id)
            if row is None:
                raise SignError("disclosure_unknown", 404)
            row["revoked_at"] = _iso(self._now())
            self.store.save_disclosure(row)
            self._audit(
                document_id=row["document_id"],
                event_type="disclosure_revoked",
                identity=actor.identity,
                auth_method="steward",
                ip=actor.ip,
                user_agent=actor.user_agent,
            )
        return {"disclosure_id": disclosure_id, "revoked": True}

    def read_share(self, token: str, *, ip: str, user_agent: str) -> tuple[bytes, str, bool]:
        """Return bytes, mode, and whether this copy is still the signed original."""
        _require()
        row = self.store.disclosure_by_token(_digest(token))
        if row is None:
            raise SignError("disclosure_unknown", 404)
        tenant = str(row["tenant_id"])

        def access(outcome: str) -> None:
            self.store.add_access(
                {
                    "id": str(uuid4()),
                    "tenant_id": tenant,
                    "disclosure_id": row["id"],
                    "ip": ip[:64],
                    "user_agent": user_agent[:300],
                    "outcome": outcome,
                    "created_at": _iso(self._now()),
                }
            )

        if row.get("revoked_at"):
            access("revoked")
            raise SignError("revoked", 410)
        if self._now() > _parse(str(row["expires_at"])):
            access("expired")
            raise SignError("expired", 410)
        doc = self.store.get_document(tenant, str(row["document_id"]))
        if doc is None:
            access("missing")
            raise SignError("document_unknown", 404)
        with _PROCESS_LOCK:
            data = self._read(doc)
            names = json.loads(row.get("redact_json") or "[]")
            original = True
            if names:
                fields = json.loads(doc.get("fields_json") or "{}")
                for name in names:
                    if name in fields:
                        fields[name] = "[redacted]"
                data = render_pdf(
                    f"{doc['title']} (redacted copy)",
                    "Redacted copy. Not the signed original.",
                    fields,
                )
                original = False
            if row.get("watermark") or row.get("mode") == "view":
                data = watermark_pdf(data, f"DISCLOSURE {str(row['id'])[:8]}")
                original = False
            access("ok")
            self._audit(
                tenant_id=tenant,
                document_id=doc["id"],
                envelope_id=str(doc.get("envelope_id") or ""),
                event_type="disclosure_viewed",
                identity="share",
                auth_method="link",
                ip=ip,
                user_agent=user_agent,
                sha256_before=str(doc["sha256"]),
                sha256_after=sha256_hex(data),
            )
        return data, str(row["mode"]), original

    def access_log(self, disclosure_id: str) -> list[dict[str, Any]]:
        _require()
        row = self.store.get_disclosure(self.tenant, disclosure_id)
        if row is None:
            raise SignError("disclosure_unknown", 404)
        return self.store.access_for(self.tenant, disclosure_id)
