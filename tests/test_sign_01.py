"""DMS_SIGN. Default off. On, PAdES sign, audit chain, disclose, verify.

The dev certificate is created in memory. No private key is written here.
"""

from __future__ import annotations

import ast
import base64
import logging
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from dms_core.signing.chain import intact
from dms_core.signing.errors import SignError
from dms_core.signing.flag import enabled
from dms_core.signing.material import ephemeral_dev_material, material_from_resolver
from dms_core.signing.pdfsign import INTENT, render_pdf, sha256_hex, verify_pdf
from dms_core.signing.sdk import SigningClient
from dms_core.signing.service import Actor, SignService
from dms_core.signing.store import memory_sign, reset_memory_sign
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ACTOR = Actor("user-1", ip="203.0.113.8", user_agent="sign-test")
CONSENT = "I agree to sign electronically"


@pytest.fixture(scope="module")
def material():
    return ephemeral_dev_material("Ada Lovelace")


@pytest.fixture(autouse=True)
def _flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SIGN", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DMS_SIGN_TSA_URL", raising=False)
    reset_memory_sign()


def _png(text: str = "Ada") -> str:
    image = Image.new("RGB", (160, 48), "white")
    ImageDraw.Draw(image).text((6, 16), text, fill="black")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _svc(material, **kwargs: object) -> SignService:
    reset_memory_sign()
    return SignService(memory_sign(), keys=lambda _tenant: material, **kwargs)  # type: ignore[arg-type]


def _envelope(svc: SignService, order: str = "sequential") -> dict:
    return svc.create_envelope(
        title="Supply agreement",
        body="Party signs below.",
        fields={"party": "Ada Lovelace", "nric": "S1234567A"},
        order=order,
        actor=ACTOR,
    )


def _sign(svc: SignService, env: dict, signer: dict, capture: dict, actor: Actor = ACTOR) -> dict:
    return svc.sign(
        env["envelope_id"],
        signer_id=signer["signer_id"],
        token=signer.get("token") or "",
        otp=signer.get("otp") or "",
        consent_text=CONSENT,
        intent=INTENT,
        capture=capture,
        actor=actor,
    )


def test_flag_is_only_the_digit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("", "0", "true", "yes", "on", "01"):
        monkeypatch.setenv("DMS_SIGN", value)
        assert enabled() is False
    monkeypatch.setenv("DMS_SIGN", "1")
    assert enabled() is True


def test_resolver_hides_secret_text() -> None:
    def boom(_ref: str) -> str:
        raise RuntimeError("BEGIN PRIVATE KEY super-secret-value")

    with pytest.raises(SignError) as caught:
        material_from_resolver("tenant-1", boom)
    assert caught.value.code == "signing_key_unavailable"
    assert "super-secret" not in str(caught.value)
    assert "PRIVATE KEY" not in repr(caught.value)


def test_resolver_accepts_pem(material) -> None:
    pem = {
        "signkey/tenant-1": material.key_pem.decode(),
        "signcert/tenant-1": material.cert_pem.decode(),
    }
    loaded = material_from_resolver("tenant-1", pem.get)
    assert repr(loaded) == "SigningMaterial(redacted)"
    assert loaded.key_pem not in repr(loaded).encode()


def test_typed_signature_verifies_and_is_visible(material) -> None:
    svc = _svc(material)
    env = _envelope(svc)
    signer = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="email_otp",
        identity="",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    signed = _sign(svc, env, signer, {"kind": "typed", "text": "Ada Lovelace"})
    assert signed["sha256_before"] != signed["sha256_after"]
    pdf = svc.document_file(env["document_id"], ACTOR)
    assert b"Ada Lovelace" in pdf
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages)
    assert "Supply agreement" in text
    checked = svc.verify(pdf)
    assert checked["hash_match"] is True
    assert checked["document_id"] == env["document_id"]
    assert checked["signatures"][0]["intact"] is True
    assert checked["signatures"][0]["valid"] is True
    assert checked["signatures"][0]["trusted"] is True
    assert checked["signatures"][0]["tsa"] is False
    tampered = bytearray(pdf)
    spot = tampered.find(b"Supply")
    tampered[spot] = ord("X")
    broken = verify_pdf(bytes(tampered), trust_pems=[material.cert_pem])
    assert broken[0]["intact"] is False


def test_drawn_and_upload_captures(material) -> None:
    svc = _svc(material)
    env = _envelope(svc, order="parallel")
    drawn = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="email_otp",
        identity="",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    _sign(svc, env, drawn, {"kind": "drawn", "png_b64": _png("draw"), "svg": svg})
    uploaded = svc.add_signer(
        env["envelope_id"],
        email="grace@example.com",
        auth_method="sso",
        identity="user-2",
        position=2,
        ttl_s=3600,
        actor=ACTOR,
    )
    _sign(
        svc,
        env,
        uploaded,
        {"kind": "upload", "png_b64": _png("up")},
        actor=Actor("user-2", ip="203.0.113.9", user_agent="upload-test"),
    )
    pdf = svc.document_file(env["document_id"], ACTOR)
    checked = svc.verify(pdf)
    assert len(checked["signatures"]) == 2
    assert all(item["intact"] and item["trusted"] for item in checked["signatures"])
    rows = svc.store.signatures_for(svc.tenant, env["document_id"])
    assert rows[0]["capture_svg"] == svg
    assert rows[1]["capture_kind"] == "upload"


def test_consent_intent_and_model_refusal(material) -> None:
    svc = _svc(material)
    env = _envelope(svc)
    signer = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="sso",
        identity="user-1",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    with pytest.raises(SignError) as missing:
        svc.sign(
            env["envelope_id"],
            signer_id=signer["signer_id"],
            token="",
            otp="",
            consent_text="",
            intent=INTENT,
            capture={"kind": "typed", "text": "Ada"},
            actor=ACTOR,
        )
    assert missing.value.code == "consent_required"
    with pytest.raises(SignError) as wrong:
        svc.sign(
            env["envelope_id"],
            signer_id=signer["signer_id"],
            token="",
            otp="",
            consent_text=CONSENT,
            intent="I agree",
            capture={"kind": "typed", "text": "Ada"},
            actor=ACTOR,
        )
    assert wrong.value.code == "intent_required"
    with pytest.raises(SignError) as model:
        svc.sign(
            env["envelope_id"],
            signer_id=signer["signer_id"],
            token="",
            otp="",
            consent_text=CONSENT,
            intent=INTENT,
            capture={"kind": "typed", "text": "Ada"},
            actor=Actor("model"),
        )
    assert model.value.code == "model_cannot_sign"
    draft = svc.draft(
        title="Notes", body="Draft only.", fields={"city": "KL"}, actor=Actor("model")
    )
    assert draft["signatures"] == []
    assert svc.verify(svc.document_file(draft["document_id"], ACTOR))["signatures"] == []


def _calls(fn: ast.FunctionDef) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            names.append(sub.func.id)
    return names


def test_sign_source_has_no_model_call() -> None:
    tree = ast.parse((ROOT / "packages/core/dms_core/signing/service.py").read_text())
    methods: list[ast.FunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "SignService":
            methods = [item for item in node.body if isinstance(item, ast.FunctionDef)]
    sign = next(item for item in methods if item.name == "sign")
    draft = next(item for item in methods if item.name == "draft")
    assert "complete" not in _calls(sign)
    assert "sign" not in _calls(draft)


def test_order_expiry_and_one_time_link(material) -> None:
    svc = _svc(material)
    env = _envelope(svc, order="sequential")
    first = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="email_otp",
        identity="",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    second = svc.add_signer(
        env["envelope_id"],
        email="grace@example.com",
        auth_method="email_otp",
        identity="",
        position=2,
        ttl_s=3600,
        actor=ACTOR,
    )
    with pytest.raises(SignError) as early:
        _sign(svc, env, second, {"kind": "typed", "text": "Grace"})
    assert early.value.code == "out_of_order"
    _sign(svc, env, first, {"kind": "typed", "text": "Ada"})
    with pytest.raises(SignError) as used:
        _sign(svc, env, first, {"kind": "typed", "text": "Ada"})
    assert used.value.code == "link_consumed"
    _sign(svc, env, second, {"kind": "typed", "text": "Grace"})
    assert svc.store.get_envelope(svc.tenant, env["envelope_id"])["status"] == "completed"

    parallel = _svc(material)
    env2 = _envelope(parallel, order="parallel")
    later = parallel.add_signer(
        env2["envelope_id"],
        email="grace@example.com",
        auth_method="email_otp",
        identity="",
        position=2,
        ttl_s=120,
        actor=ACTOR,
    )
    _sign(parallel, env2, later, {"kind": "typed", "text": "Grace"})
    start = datetime(2026, 1, 1, tzinfo=UTC)
    expiring = _svc(material, clock=lambda: start)
    env3 = _envelope(expiring)
    old = expiring.add_signer(
        env3["envelope_id"],
        email="ada@example.com",
        auth_method="email_otp",
        identity="",
        position=1,
        ttl_s=60,
        actor=ACTOR,
    )
    expiring._clock = lambda: start + timedelta(seconds=120)
    with pytest.raises(SignError) as expired:
        _sign(expiring, env3, old, {"kind": "typed", "text": "Ada"})
    assert expired.value.code == "link_expired"


def test_audit_chain_records_identity_and_breaks_when_edited(material) -> None:
    svc = _svc(material)
    seen: list[dict] = []

    def ledger(event_type: str, payload: dict, actor: str) -> str:
        seen.append({"event_type": event_type, "payload": payload, "actor": actor})
        return "cortex-entry-1"

    svc.ledger = ledger
    env = _envelope(svc)
    signer = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="sso",
        identity="user-1",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    _sign(svc, env, signer, {"kind": "typed", "text": "Ada Lovelace"})
    svc.document_file(env["document_id"], ACTOR)
    body = svc.audit(env["document_id"])
    assert body["chain_ok"] is True
    kinds = [row["event_type"] for row in body["events"]]
    assert kinds == ["document_created", "signer_added", "signed", "viewed"]
    signed = next(row for row in body["events"] if row["event_type"] == "signed")
    assert signed["identity"] == "user-1"
    assert signed["auth_method"] == "sso"
    assert signed["ip"] == "203.0.113.8"
    assert signed["user_agent"] == "sign-test"
    assert signed["consent_text"] == CONSENT
    assert signed["cortex_entry_id"] == "cortex-entry-1"
    assert signed["sha256_before"] != signed["sha256_after"]
    blob = "\n".join(str(item["payload"]) for item in seen)
    assert "PRIVATE" not in blob
    assert material.key_pem.decode() not in blob
    svc.store.audits[2]["consent_text"] = "edited"
    assert intact(svc.store.audits_for(svc.tenant, env["document_id"])) is False


def test_ledger_pointer_is_off_unless_the_digit_one(monkeypatch) -> None:
    from dms_api.wiring import sign_ledger_callback

    monkeypatch.delenv("DMS_SIGN_LEDGER", raising=False)
    assert sign_ledger_callback(object()) is None
    monkeypatch.setenv("DMS_SIGN_LEDGER", "yes")
    assert sign_ledger_callback(object()) is None
    monkeypatch.setenv("DMS_SIGN_LEDGER", "1")
    assert sign_ledger_callback(None) is None
    seen: list[dict] = []

    class _Client:
        def ledger_append(self, request):
            seen.append(request.payload)

            class _Reply:
                entry_id = "entry-9"

            return _Reply()

    record = sign_ledger_callback(_Client())
    assert record("signed", {"event_hash": "abc", "identity": "ada"}, "ada") == "entry-9"
    assert seen == [{"event_hash": "abc", "identity": "ada"}]
    assert "private_key" not in seen[0]


def test_disclose_redact_watermark_revoke(material) -> None:
    svc = _svc(material)
    env = _envelope(svc)
    signer = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="sso",
        identity="user-1",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    _sign(svc, env, signer, {"kind": "typed", "text": "Ada"})
    original = svc.document_file(env["document_id"], ACTOR)
    view = svc.disclose(
        env["document_id"],
        mode="view",
        watermark=False,
        redact_fields=["nric"],
        ttl_s=600,
        actor=ACTOR,
    )
    data, mode, is_original = svc.read_share(view["token"], ip="198.51.100.4", user_agent="viewer")
    assert mode == "view"
    assert is_original is False
    assert b"S1234567A" not in data
    assert b"DISCLOSURE" in data
    assert data != original
    assert sha256_hex(data) != sha256_hex(original)
    download = svc.disclose(
        env["document_id"],
        mode="download",
        watermark=False,
        redact_fields=[],
        ttl_s=600,
        actor=ACTOR,
    )
    raw, mode, is_original = svc.read_share(
        download["token"], ip="198.51.100.4", user_agent="viewer"
    )
    assert mode == "download"
    assert is_original is True
    assert raw == original
    svc.revoke(view["disclosure_id"], ACTOR)
    with pytest.raises(SignError) as revoked:
        svc.read_share(view["token"], ip="198.51.100.4", user_agent="viewer")
    assert revoked.value.code == "revoked"
    outcomes = [row["outcome"] for row in svc.access_log(view["disclosure_id"])]
    assert outcomes == ["ok", "revoked"]


def test_object_store_keeps_hash_not_a_second_blob(material) -> None:
    class _Objects:
        def __init__(self) -> None:
            self.data: dict[str, bytes] = {}

        def put(self, key: str, data: bytes) -> str:
            self.data[key] = data
            return key

        def get(self, key: str) -> bytes:
            return self.data[key]

    objects = _Objects()
    svc = _svc(material, objects=objects)
    env = _envelope(svc)
    doc = svc.store.get_document(svc.tenant, env["document_id"])
    assert doc["storage"] == "object_store"
    assert not doc["blob"]
    assert objects.get(doc["object_key"])[:4] == b"%PDF"
    signer = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="sso",
        identity="user-1",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    _sign(svc, env, signer, {"kind": "typed", "text": "Ada"})
    pdf = svc.document_file(env["document_id"], ACTOR)
    assert svc.verify(pdf)["hash_match"] is True


def test_key_material_is_not_logged(material) -> None:
    handler_records: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            handler_records.append(record.getMessage())

    grab = _Grab()
    root = logging.getLogger()
    root.addHandler(grab)
    try:
        svc = _svc(material)
        env = _envelope(svc)
        signer = svc.add_signer(
            env["envelope_id"],
            email="ada@example.com",
            auth_method="sso",
            identity="user-1",
            position=1,
            ttl_s=3600,
            actor=ACTOR,
        )
        _sign(svc, env, signer, {"kind": "typed", "text": "Ada"})
    finally:
        root.removeHandler(grab)
    text = "\n".join(handler_records)
    assert material.key_pem.decode() not in text


def test_timestamp_when_a_stamper_is_configured(material) -> None:
    from pyhanko.keys import load_certs_from_pemder_data, load_private_key_from_pemder_data
    from pyhanko.sign.timestamps import DummyTimeStamper

    stamper = DummyTimeStamper(
        list(load_certs_from_pemder_data(material.cert_pem))[0],
        load_private_key_from_pemder_data(material.key_pem, None),
    )
    svc = _svc(material)
    env = _envelope(svc)
    signer = svc.add_signer(
        env["envelope_id"],
        email="ada@example.com",
        auth_method="sso",
        identity="user-1",
        position=1,
        ttl_s=3600,
        actor=ACTOR,
    )
    signed = svc.sign(
        env["envelope_id"],
        signer_id=signer["signer_id"],
        token="",
        otp="",
        consent_text=CONSENT,
        intent=INTENT,
        capture={"kind": "typed", "text": "Ada"},
        actor=ACTOR,
        timestamper=stamper,
    )
    assert signed["tsa"] is True
    pdf = svc.document_file(env["document_id"], ACTOR)
    assert svc.verify(pdf)["signatures"][0]["tsa"] is True


def test_migration_creates_the_signing_tables() -> None:
    text = (ROOT / "alembic/versions/0012_sign.py").read_text(encoding="utf-8")
    for name in (
        "sign_documents",
        "sign_versions",
        "sign_signatures",
        "sign_audit_events",
        "sign_disclosures",
        "sign_access_log",
    ):
        assert name in text
    ledger = (ROOT / "alembic/versions/0001_dms_control_plane.py").read_text(encoding="utf-8")
    assert "no local hash chain" in ledger


def test_sdk_paths() -> None:
    calls: list[tuple[str, str]] = []

    def opener(method: str, url: str, body: bytes | None, headers: dict[str, str]):
        del body, headers
        calls.append((method, url))
        return 200, b'{"ok": true, "envelope_id": "e", "document_id": "d", "signer_id": "s"}'

    client = SigningClient("http://dms.example", opener=opener)
    client.create_envelope(title="A", order="parallel")
    client.add_signer("e", email="ada@example.com", auth_method="sso", identity="user-1")
    client.sign(
        "e",
        signer_id="s",
        consent_text=CONSENT,
        intent=INTENT,
        capture={"kind": "typed", "text": "Ada"},
    )
    client.verify(pdf_b64="AAAA")
    client.disclose("d", mode="view", redact_fields=["nric"])
    client.draft(title="Notes", body="Hello", fields={"city": "KL"})
    paths = [url for _method, url in calls]
    assert paths == [
        "http://dms.example/v1/sign/envelopes",
        "http://dms.example/v1/sign/envelopes/e/signers",
        "http://dms.example/v1/sign/envelopes/e/sign",
        "http://dms.example/v1/sign/verify",
        "http://dms.example/v1/sign/documents/d/disclose",
        "http://dms.example/v1/sign/drafts",
    ]


def test_ui_consent_sentence_matches_server() -> None:
    text = (ROOT / "apps/ui/src/lib/signCapture.ts").read_text(encoding="utf-8")
    assert INTENT in text
    panel = (ROOT / "apps/ui/src/pages/SignPanel.tsx").read_text(encoding="utf-8")
    assert "if (!enabled) return null" in panel


def test_flag_off_route_is_404(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DMS_SIGN", raising=False)
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    get_settings.cache_clear()
    client = TestClient(create_app())
    assert client.get("/v1/sign/status").status_code == 404
    assert client.post("/v1/sign/envelopes", json={"title": "A"}).status_code == 404


def test_route_signs_verifies_and_writes_sample(material, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_SIGN", "1")
    from cortex_client.gate import ComplianceDecision
    from dms_api.app import create_app
    from dms_api.settings import get_settings
    from fastapi.testclient import TestClient

    get_settings.cache_clear()

    def allow(*, action: str, actor: str | None = None, **_kwargs: object) -> ComplianceDecision:
        del actor
        return ComplianceDecision(allowed=True, reason="test_allow", action=action)

    app = create_app()
    app.state.sign_key_loader = lambda _tenant: material
    monkeypatch.setattr("dms_api.routes.sign.compliance_gate", allow)
    client = TestClient(app)
    created = client.post(
        "/v1/sign/envelopes",
        json={
            "title": "Sample",
            "body": "Signed sample for verification.",
            "fields": {"party": "Ada"},
            "order": "sequential",
        },
    )
    assert created.status_code == 200, created.text
    env = created.json()
    added = client.post(
        f"/v1/sign/envelopes/{env['envelope_id']}/signers",
        json={
            "email": "ada@example.com",
            "auth_method": "sso",
            "identity": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "position": 1,
            "ttl_s": 3600,
        },
    )
    assert added.status_code == 200, added.text
    signer = added.json()
    done = client.post(
        f"/v1/sign/links/{signer.get('token') or 'sso'}/sign",
        json={
            "signer_id": signer["signer_id"],
            "consent_text": CONSENT,
            "intent": INTENT,
            "capture": {"kind": "typed", "text": "Ada Lovelace"},
        },
    )
    # SSO has no token. The link path still accepts the signer id.
    if done.status_code != 200:
        done = client.post(
            f"/v1/sign/envelopes/{env['envelope_id']}/sign",
            json={
                "signer_id": signer["signer_id"],
                "consent_text": CONSENT,
                "intent": INTENT,
                "capture": {"kind": "typed", "text": "Ada Lovelace"},
            },
        )
    assert done.status_code == 200, done.text
    pdf = client.get(f"/v1/sign/documents/{env['document_id']}/file")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    upload = client.post(
        "/v1/sign/verify/upload",
        files={"file": ("sample.pdf", pdf.content, "application/pdf")},
    )
    assert upload.status_code == 200, upload.text
    body = upload.json()
    assert body["hash_match"] is True
    assert body["signatures"][0]["intact"] is True
    assert body["signatures"][0]["trusted"] is True
    sample = Path("/opt/cursor/artifacts/sample_signed.pdf")
    sample.parent.mkdir(parents=True, exist_ok=True)
    sample.write_bytes(pdf.content)
    encoded = base64.b64encode(sample.read_bytes()).decode()
    again = client.post("/v1/sign/verify", json={"pdf_b64": encoded})
    assert again.status_code == 200
    assert again.json()["signatures"][0]["valid"] is True
    shared = client.post(
        f"/v1/sign/documents/{env['document_id']}/disclose",
        json={"mode": "view", "watermark": True, "redact_fields": [], "ttl_s": 600},
    )
    assert shared.status_code == 200, shared.text
    token = shared.json()["token"]
    view = client.get(f"/v1/sign/share/{token}")
    assert view.status_code == 200
    assert view.headers["x-dms-signed-original"] == "false"
    assert b"DISCLOSURE" in view.content
    refused = client.post(
        "/v1/sign/envelopes",
        json={"title": "No", "private_key": "secret"},
    )
    assert refused.status_code == 422
    assert "secret" not in refused.text
    # An unsigned render is not a signature.
    bare = render_pdf("Plain", "No signature.", {})
    assert bare.startswith(b"%PDF")
