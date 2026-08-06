"""What the Spaces catalog is actually bound to, and whether it survives a restart.

``persisted`` used to be ``bool(settings.database_url)``. That is a claim about
configuration, not about storage. Startup probes Postgres and falls back to the
in-process store on any failure, so with ``DATABASE_URL`` set and Postgres down
the API went on telling callers their Space was persisted — and the Space then
vanished on the next restart, having been announced as durable. The failure was
visible only as a warning in a log nobody reads.

R-0011: a silent fallback is a lie. The binding that actually happened is
recorded here at startup, and every honesty field on the wire reads from it
rather than from the setting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoreBinding:
    """The outcome of binding the Spaces catalog at startup."""

    backend: str
    """``postgres`` or ``memory`` — what is serving reads and writes right now."""

    persistent: bool
    """True only when writes survive a process restart."""

    configured: bool = False
    """Whether ``DATABASE_URL`` was set. Differs from :attr:`persistent` exactly
    when the operator asked for Postgres and did not get it — the case worth
    reporting loudly."""

    reason: str | None = None
    """Why the fallback happened, when it did."""

    @classmethod
    def postgres(cls) -> StoreBinding:
        return cls(backend="postgres", persistent=True, configured=True)

    @classmethod
    def memory(cls, *, configured: bool, reason: str | None = None) -> StoreBinding:
        return cls(backend="memory", persistent=False, configured=configured, reason=reason)

    @property
    def hint(self) -> str | None:
        """One line the UI can show. ``None`` only when storage is genuinely durable."""
        if self.persistent:
            return None
        if self.configured:
            return (
                "DATABASE_URL is set but Postgres could not be reached, so this is an "
                "in-memory Space that will not survive a restart"
                + (f" ({self.reason})" if self.reason else "")
                + "."
            )
        return "In-memory Space — set DATABASE_URL for a Space that survives restart."

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "persistent": self.persistent,
            "configured": self.configured,
            "reason": self.reason,
        }
