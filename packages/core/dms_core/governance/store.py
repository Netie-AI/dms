"""In-process governance records. Postgres is a write-through when it is configured.

No secret value is stored. Findings keep a type and a file hash.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class Assignment:
    tenant_id: str
    user_id: str
    email: str
    role: str
    daily_usd: float | None
    monthly_usd: float | None
    daily_tokens: int | None
    monthly_tokens: int | None
    allow_ask: bool = True
    allow_create: bool = True
    allow_connectors: bool = True
    updated_by: str = ""


@dataclass(frozen=True)
class Policy:
    upload: str
    redact_sensitive: bool


@dataclass(frozen=True)
class Spend:
    event_id: str
    tenant_id: str
    user_id: str
    at: datetime
    tokens: int
    cost_usd: float | None
    model: str
    source: str


@dataclass(frozen=True)
class Finding:
    finding_id: str
    tenant_id: str
    user_id: str
    file_sha256: str
    filename: str
    finding_type: str
    action: str
    at: datetime


@dataclass(frozen=True)
class Alert:
    alert_id: str
    tenant_id: str
    user_id: str
    kind: str
    summary: str
    at: datetime


@dataclass(frozen=True)
class AskCost:
    ask_id: str
    cost_usd: float | None
    model: str
    tokens: int


@dataclass
class GovStore:
    """Process memory, optionally mirrored to ``dms.gov_*``."""

    database_url: str | None = None
    persist_error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _assignments: dict[tuple[str, str], Assignment] = field(default_factory=dict)
    _policies: dict[str, Policy] = field(default_factory=dict)
    _spend: dict[str, Spend] = field(default_factory=dict)
    _findings: list[Finding] = field(default_factory=list)
    _alerts: list[Alert] = field(default_factory=list)
    _costs: dict[str, AskCost] = field(default_factory=dict)
    _readback_ok: set[tuple[str, str]] = field(default_factory=set)

    def bind(self, database_url: str | None, tenant_id: str) -> None:
        """Load rows when Postgres is configured. A failure stays on memory."""
        url = (database_url or "").strip()
        self.database_url = url or None
        if not url:
            return
        try:
            UUID(tenant_id)
        except (TypeError, ValueError):
            self.persist_error = "tenant"
            return
        try:
            self._load(tenant_id)
            self.persist_error = ""
        except Exception as exc:  # noqa: BLE001 - the type is the log, not the DSN
            self.persist_error = type(exc).__name__
            logger.warning("gov store load failed (%s)", type(exc).__name__)

    def assignment(self, tenant_id: str, user_id: str) -> Assignment | None:
        with self._lock:
            row = self._assignments.get((tenant_id, user_id))
            return None if row is None else Assignment(**row.__dict__)

    def assignments(self, tenant_id: str) -> list[Assignment]:
        with self._lock:
            rows = [row for (tenant, _), row in self._assignments.items() if tenant == tenant_id]
        rows.sort(key=lambda row: row.user_id)
        return rows

    def upsert_assignment(self, row: Assignment) -> None:
        with self._lock:
            self._assignments[(row.tenant_id, row.user_id)] = row
        self._write_assignment(row)

    def upload_rule(self, tenant_id: str) -> Policy | None:
        with self._lock:
            return self._policies.get(tenant_id)

    def set_upload_rule(self, tenant_id: str, policy: Policy) -> None:
        with self._lock:
            self._policies[tenant_id] = policy
        self._write_upload_rule(tenant_id, policy)

    def add_spend(self, row: Spend) -> None:
        with self._lock:
            if row.event_id in self._spend:
                return
            self._spend[row.event_id] = row
        self._write_spend(row)

    def spend_for(self, tenant_id: str, user_id: str | None = None) -> list[Spend]:
        with self._lock:
            rows = [
                row
                for row in self._spend.values()
                if row.tenant_id == tenant_id and (user_id is None or row.user_id == user_id)
            ]
        rows.sort(key=lambda row: (row.at, row.event_id))
        return rows

    def mark_readback(self, tenant_id: str, user_id: str, ok: bool) -> None:
        key = (tenant_id, user_id)
        with self._lock:
            if ok:
                self._readback_ok.add(key)
            else:
                self._readback_ok.discard(key)

    def readback_ok(self, tenant_id: str, user_id: str) -> bool:
        with self._lock:
            return (tenant_id, user_id) in self._readback_ok

    def add_finding(self, row: Finding) -> None:
        with self._lock:
            self._findings.append(row)
        self._write_finding(row)

    def findings(self, tenant_id: str) -> list[Finding]:
        with self._lock:
            rows = [row for row in self._findings if row.tenant_id == tenant_id]
        rows.sort(key=lambda row: row.at, reverse=True)
        return rows

    def add_alert(self, row: Alert) -> None:
        with self._lock:
            self._alerts.append(row)
        self._write_alert(row)

    def alerts(self, tenant_id: str) -> list[Alert]:
        with self._lock:
            rows = [row for row in self._alerts if row.tenant_id == tenant_id]
        rows.sort(key=lambda row: row.at, reverse=True)
        return rows

    def remember_cost(self, row: AskCost) -> None:
        with self._lock:
            self._costs[row.ask_id] = row

    def cost_for(self, ask_id: str) -> AskCost | None:
        with self._lock:
            return self._costs.get(ask_id)

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.database_url or "", connect_timeout=3)

    def _load(self, tenant_id: str) -> None:
        with self._connect() as conn:
            assignments = conn.execute(
                """
                SELECT user_id, email, gov_role, daily_usd, monthly_usd,
                       daily_tokens, monthly_tokens, allow_ask, allow_create,
                       allow_connectors, updated_by
                  FROM dms.gov_assignment
                 WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchall()
            for raw in assignments:
                row = Assignment(
                    tenant_id=tenant_id,
                    user_id=raw[0],
                    email=raw[1] or "",
                    role=raw[2],
                    daily_usd=raw[3],
                    monthly_usd=raw[4],
                    daily_tokens=None if raw[5] is None else int(raw[5]),
                    monthly_tokens=None if raw[6] is None else int(raw[6]),
                    allow_ask=bool(raw[7]),
                    allow_create=bool(raw[8]),
                    allow_connectors=bool(raw[9]),
                    updated_by=raw[10] or "",
                )
                self._assignments[(tenant_id, row.user_id)] = row
            policy = conn.execute(
                """
                SELECT upload_action, redact_sensitive
                  FROM dms.gov_policy
                 WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
            if policy is not None:
                self._policies[tenant_id] = Policy(policy[0], bool(policy[1]))
            conn.commit()

    def _write_assignment(self, row: Assignment) -> None:
        if not self.database_url:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dms.gov_assignment (
                      tenant_id, user_id, email, gov_role, daily_usd, monthly_usd,
                      daily_tokens, monthly_tokens, allow_ask, allow_create,
                      allow_connectors, updated_at, updated_by
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
                    ON CONFLICT (tenant_id, user_id) DO UPDATE SET
                      email = EXCLUDED.email,
                      gov_role = EXCLUDED.gov_role,
                      daily_usd = EXCLUDED.daily_usd,
                      monthly_usd = EXCLUDED.monthly_usd,
                      daily_tokens = EXCLUDED.daily_tokens,
                      monthly_tokens = EXCLUDED.monthly_tokens,
                      allow_ask = EXCLUDED.allow_ask,
                      allow_create = EXCLUDED.allow_create,
                      allow_connectors = EXCLUDED.allow_connectors,
                      updated_at = now(),
                      updated_by = EXCLUDED.updated_by
                    """,
                    (
                        row.tenant_id,
                        row.user_id,
                        row.email,
                        row.role,
                        row.daily_usd,
                        row.monthly_usd,
                        row.daily_tokens,
                        row.monthly_tokens,
                        row.allow_ask,
                        row.allow_create,
                        row.allow_connectors,
                        row.updated_by,
                    ),
                )
                conn.commit()
            self.persist_error = ""
        except Exception as exc:  # noqa: BLE001
            self.persist_error = type(exc).__name__
            logger.warning("gov assignment not persisted (%s)", type(exc).__name__)

    def _write_upload_rule(self, tenant_id: str, policy: Policy) -> None:
        if not self.database_url:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dms.gov_policy (tenant_id, upload_action, redact_sensitive)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (tenant_id) DO UPDATE SET
                      upload_action = EXCLUDED.upload_action,
                      redact_sensitive = EXCLUDED.redact_sensitive
                    """,
                    (tenant_id, policy.upload, policy.redact_sensitive),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            self.persist_error = type(exc).__name__
            logger.warning("gov policy not persisted (%s)", type(exc).__name__)

    def _write_spend(self, row: Spend) -> None:
        if not self.database_url:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dms.gov_spend (
                      event_id, tenant_id, user_id, spent_at, tokens, cost_usd, model, source
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        row.event_id,
                        row.tenant_id,
                        row.user_id,
                        row.at,
                        row.tokens,
                        row.cost_usd,
                        row.model,
                        row.source,
                    ),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            self.persist_error = type(exc).__name__
            logger.warning("gov spend not persisted (%s)", type(exc).__name__)

    def _write_finding(self, row: Finding) -> None:
        if not self.database_url:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dms.gov_finding (
                      finding_id, tenant_id, user_id, file_sha256, filename,
                      finding_type, action, created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        row.finding_id,
                        row.tenant_id,
                        row.user_id,
                        row.file_sha256,
                        row.filename,
                        row.finding_type,
                        row.action,
                        row.at,
                    ),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            self.persist_error = type(exc).__name__
            logger.warning("gov finding not persisted (%s)", type(exc).__name__)

    def _write_alert(self, row: Alert) -> None:
        if not self.database_url:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dms.gov_alert (
                      alert_id, tenant_id, user_id, kind, summary, created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (row.alert_id, row.tenant_id, row.user_id, row.kind, row.summary, row.at),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            self.persist_error = type(exc).__name__
            logger.warning("gov alert not persisted (%s)", type(exc).__name__)


_STORE = GovStore()


def get_store() -> GovStore:
    return _STORE


def reset_store() -> GovStore:
    """Test helper. Drops process rows and any database binding."""
    global _STORE
    _STORE = GovStore()
    return _STORE
