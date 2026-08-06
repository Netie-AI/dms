"""Alembic upgrade helper for API lifespan."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def run_migrations(database_url: str) -> None:
    from alembic.config import Config

    from alembic import command

    root = Path(__file__).resolve().parents[3]  # repo root
    ini = root / "alembic.ini"
    if not ini.is_file():
        logger.warning("alembic.ini missing at %s — skip migrate", ini)
        return
    os.environ["DATABASE_URL"] = database_url
    cfg = Config(str(ini))
    command.upgrade(cfg, "head")
    logger.info("alembic upgrade head ok")
