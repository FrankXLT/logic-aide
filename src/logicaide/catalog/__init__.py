"""SQLite relational catalog package for LogicAIDE."""

from __future__ import annotations

from logicaide.catalog.sqlite_store import (
    DEFAULT_CATALOG_DB,
    connect_catalog,
    get_vehicle_and_latest_revision,
    init_catalog_db,
    persist_compiled_profile,
    record_profile_revision,
    upsert_vehicle,
)

__all__ = [
    "DEFAULT_CATALOG_DB",
    "connect_catalog",
    "get_vehicle_and_latest_revision",
    "init_catalog_db",
    "persist_compiled_profile",
    "record_profile_revision",
    "upsert_vehicle",
]
