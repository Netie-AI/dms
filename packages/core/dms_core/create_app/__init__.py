"""Runnable Create apps (DMS-CREATE-APP-01).

DMS is the harness. A provider writes the app. This package checks the
bundle, scopes data access, confirms actions, and runs the backend in a
sandbox. It does not invent screens from phrases.
"""

from dms_core.create_app.flags import enabled, salesforce_enabled

__all__ = ["enabled", "salesforce_enabled"]
