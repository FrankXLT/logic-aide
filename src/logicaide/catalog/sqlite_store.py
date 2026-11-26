"""SQLite relational catalog store for LogicAIDE vehicles, profiles, and lake metadata."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

DEFAULT_CATALOG_DB = Path("data/drives/catalog.sqlite")

CATALOG_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vehicles (
    car_id TEXT PRIMARY KEY, vin_hash TEXT UNIQUE,
    year INTEGER NOT NULL CHECK (year BETWEEN 1984 AND 1995),
    model TEXT NOT NULL,
    ecu_family TEXT NOT NULL CHECK (ecu_family IN ('LM_PM', 'SMEC_DUAL_BOARD', 'SBEC_SINGLE_BOARD')),
    engine_profile_id TEXT NOT NULL, transaxle_code TEXT NOT NULL, turbo_spec_id TEXT,
    pinout_verified INTEGER NOT NULL DEFAULT 0 CHECK (pinout_verified IN (0, 1)),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vehicle_profile_revisions (
    revision_id TEXT PRIMARY KEY,
    car_id TEXT NOT NULL REFERENCES vehicles(car_id),
    schema_version TEXT NOT NULL DEFAULT '2020-12',
    compatibility_tier TEXT NOT NULL CHECK (compatibility_tier IN ('BOLT_ON_NATIVE', 'HYBRID_MACHINING_REQUIRED')),
    profile_sha256 TEXT NOT NULL, profile_json TEXT NOT NULL,
    swap_checklist_json TEXT NOT NULL DEFAULT '[]', compiled_at_utc TEXT NOT NULL,
    UNIQUE (car_id, profile_sha256)
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY, car_id TEXT NOT NULL REFERENCES vehicles(car_id),
    build_id TEXT NOT NULL DEFAULT 'STOCK', started_at_utc TEXT NOT NULL, ended_at_utc TEXT,
    run_kind TEXT NOT NULL CHECK (run_kind IN ('VEHICLE_SCI', 'SINGLE_REPLAY', 'MONTE_CARLO_SWEEP')),
    rom_sha256 TEXT NOT NULL, baro_kpa REAL NOT NULL DEFAULT 101.325,
    quarantined_cells_json TEXT NOT NULL DEFAULT '[]',
    imu_attached INTEGER NOT NULL DEFAULT 0 CHECK (imu_attached IN (0, 1)),
    gps_stripped INTEGER NOT NULL DEFAULT 1 CHECK (gps_stripped IN (0, 1)), notes TEXT
);
CREATE TABLE IF NOT EXISTS bookmarks (
    bookmark_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
    ts_utc_ns INTEGER NOT NULL, sample_idx INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('SPACEBAR', 'TOUCH', 'AUTO_ANOMALY')),
    label TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS tuning_issues (
    issue_id TEXT PRIMARY KEY, car_id TEXT NOT NULL REFERENCES vehicles(car_id),
    session_id TEXT REFERENCES sessions(session_id), build_id TEXT, campaign_id TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('INFO', 'CAUTION', 'CRITICAL')),
    category TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'IN_PROGRESS', 'VERIFIED_FIXED', 'WONT_FIX')),
    title TEXT NOT NULL, description TEXT NOT NULL,
    rpm_min REAL, rpm_max REAL, map_min_kpa REAL, map_max_kpa REAL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS optimization_campaigns (
    campaign_id TEXT PRIMARY KEY, car_id TEXT NOT NULL REFERENCES vehicles(car_id),
    baseline_rom_sha256 TEXT NOT NULL, issue_id TEXT REFERENCES tuning_issues(issue_id),
    sampler TEXT NOT NULL CHECK (sampler IN ('SOBOL', 'LHS', 'OPTUNA')),
    n_trials INTEGER NOT NULL CHECK (n_trials > 0),
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    objective_summary TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parquet_chunks (
    chunk_id TEXT PRIMARY KEY, session_id TEXT REFERENCES sessions(session_id),
    car_id TEXT NOT NULL REFERENCES vehicles(car_id), build_id TEXT NOT NULL DEFAULT 'STOCK',
    campaign_id TEXT REFERENCES optimization_campaigns(campaign_id),
    run_kind TEXT NOT NULL CHECK (run_kind IN ('VEHICLE_SCI', 'SINGLE_REPLAY', 'MONTE_CARLO_SWEEP')),
    file_path TEXT UNIQUE NOT NULL, row_count INTEGER NOT NULL CHECK (row_count >= 0),
    t_start_ns INTEGER NOT NULL, t_end_ns INTEGER NOT NULL,
    rpm_min REAL, rpm_max REAL, map_min_kpa REAL, map_max_kpa REAL,
    zstd_level INTEGER NOT NULL DEFAULT 3, sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stimulus_fixtures (
    fixture_id TEXT PRIMARY KEY, car_id TEXT NOT NULL REFERENCES vehicles(car_id),
    source_session_id TEXT REFERENCES sessions(session_id), issue_id TEXT REFERENCES tuning_issues(issue_id),
    file_path TEXT UNIQUE NOT NULL, gps_stripped INTEGER NOT NULL DEFAULT 1 CHECK (gps_stripped = 1),
    sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_builds (
    build_id TEXT PRIMARY KEY, car_id TEXT NOT NULL REFERENCES vehicles(car_id),
    campaign_id TEXT REFERENCES optimization_campaigns(campaign_id),
    baseline_sha256 TEXT NOT NULL, candidate_sha256 TEXT NOT NULL,
    staging_dir TEXT UNIQUE NOT NULL,
    vectors_ok INTEGER NOT NULL CHECK (vectors_ok IN (0, 1)), max_loop_cycles INTEGER NOT NULL,
    twin_parity_ok INTEGER NOT NULL CHECK (twin_parity_ok IN (0, 1)), sweep_overflows INTEGER NOT NULL,
    safety_gate_status TEXT NOT NULL CHECK (safety_gate_status IN ('PASS', 'BLOCKED')),
    created_at TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    """Return the current UTC timestamp formatted as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _derive_vin_hash(car_id: str) -> str:
    """Derive a deterministic SHA-256 VIN surrogate when no VIN hash is supplied."""
    return hashlib.sha256(f"logicaide:vin:{car_id}".encode("utf-8")).hexdigest()


def _normalize_revision_payload(revision: dict[str, Any]) -> dict[str, Any]:
    """Normalize a vehicle profile revision payload for SQLite persistence."""
    car_id = str(revision["car_id"])
    raw_profile_json = revision.get("profile_json")
    if isinstance(raw_profile_json, str):
        profile_json = raw_profile_json
    elif revision.get("profile") is not None:
        profile_json = json.dumps(revision["profile"], sort_keys=True, separators=(",", ":"))
    else:
        raise ValueError("Revision payload must provide 'profile_json' or 'profile'.")

    profile_sha256 = str(
        revision.get("profile_sha256")
        or hashlib.sha256(profile_json.encode("utf-8")).hexdigest()
    )

    raw_checklist_json = revision.get("swap_checklist_json")
    if isinstance(raw_checklist_json, str):
        swap_checklist_json = raw_checklist_json
    elif raw_checklist_json is not None:
        swap_checklist_json = json.dumps(raw_checklist_json, sort_keys=True)
    elif revision.get("swap_checklist") is not None:
        swap_checklist_json = json.dumps(revision["swap_checklist"], sort_keys=True)
    else:
        swap_checklist_json = "[]"

    return {
        "revision_id": str(revision.get("revision_id") or uuid.uuid4()),
        "car_id": car_id,
        "schema_version": str(revision.get("schema_version") or "2020-12"),
        "compatibility_tier": str(revision["compatibility_tier"]),
        "profile_sha256": profile_sha256,
        "profile_json": profile_json,
        "swap_checklist_json": swap_checklist_json,
        "compiled_at_utc": str(revision.get("compiled_at_utc") or _utc_now_iso()),
    }


def _extract_vehicle_payload(compiled: dict[str, Any]) -> dict[str, Any]:
    """Extract a ``vehicles`` table payload from a compiled or raw build dict."""
    if isinstance(compiled.get("vehicle"), dict):
        return dict(compiled["vehicle"])

    profile = compiled.get("profile") if isinstance(compiled.get("profile"), dict) else compiled
    identity = profile.get("identity_and_chassis") or {}
    bottom = profile.get("engine_bottom_end") or {}
    head = profile.get("cylinder_head_and_cam") or {}
    trans = profile.get("transaxle_and_gearing") or {}
    turbo = profile.get("turbo_and_intercooler") or {}
    ecu = profile.get("ecu_and_sensors") or {}

    block_code, head_code = bottom.get("block_code"), head.get("head_code")
    default_engine_id = f"{block_code}+{head_code}" if block_code and head_code else block_code
    pinout_verified = (
        int(bool(compiled["pinout_verified"]))
        if "pinout_verified" in compiled
        else int(
            bool(ecu.get("pin11_continuity_verified", False))
            and bool(ecu.get("pin2_3_25_45_verified", False))
        )
    )
    return {
        "car_id": compiled.get("car_id") or identity["car_id"],
        "vin_hash": compiled.get("vin_hash") or identity.get("vin_hash"),
        "year": compiled.get("year") or identity["year"],
        "model": compiled.get("model") or identity["model"],
        "ecu_family": compiled.get("ecu_family") or ecu["ecu_family"],
        "engine_profile_id": compiled.get("engine_profile_id") or default_engine_id,
        "transaxle_code": compiled.get("transaxle_code") or trans["transaxle_code"],
        "turbo_spec_id": compiled.get("turbo_spec_id") or turbo.get("turbo_id") or turbo.get("turbo_spec_ref"),
        "pinout_verified": pinout_verified,
        "created_at": compiled.get("created_at"),
    }


def _extract_revision_payload(compiled: dict[str, Any], car_id: str) -> dict[str, Any]:
    """Extract and normalize a ``vehicle_profile_revisions`` payload from compiled output."""
    if isinstance(compiled.get("revision"), dict):
        revision = dict(compiled["revision"])
        revision.setdefault("car_id", car_id)
        return _normalize_revision_payload(revision)

    profile = compiled.get("profile") if isinstance(compiled.get("profile"), dict) else compiled
    head = profile.get("cylinder_head_and_cam") or {}
    return _normalize_revision_payload(
        {
            "revision_id": compiled.get("revision_id"),
            "car_id": car_id,
            "schema_version": compiled.get("schema_version") or "2020-12",
            "compatibility_tier": compiled.get("compatibility_tier") or head["compatibility_tier"],
            "profile_sha256": compiled.get("profile_sha256"),
            "profile_json": compiled.get("profile_json"),
            "profile": profile,
            "swap_checklist_json": compiled.get("swap_checklist_json"),
            "swap_checklist": compiled.get("swap_checklist") or head.get("required_swap_mods") or [],
            "compiled_at_utc": compiled.get("compiled_at_utc"),
        }
    )


def init_catalog_db(db_path: Path | str = DEFAULT_CATALOG_DB) -> Path:
    """Initialize the LogicAIDE SQLite catalog schema and return its path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(CATALOG_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
    return path


def connect_catalog(db_path: Path | str = DEFAULT_CATALOG_DB) -> sqlite3.Connection:
    """Ensure catalog schema exists and return a configured SQLite connection."""
    path = init_catalog_db(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def upsert_vehicle(conn: sqlite3.Connection, vehicle: dict[str, Any]) -> None:
    """Insert or update a ``vehicles`` record keyed by ``car_id``."""
    car_id = str(vehicle["car_id"])
    raw_vin_hash = vehicle.get("vin_hash")
    vin_hash = (
        str(raw_vin_hash).strip()
        if raw_vin_hash is not None and str(raw_vin_hash).strip()
        else _derive_vin_hash(car_id)
    )
    created_at = str(vehicle.get("created_at") or _utc_now_iso())
    turbo_spec_id = str(vehicle["turbo_spec_id"]) if vehicle.get("turbo_spec_id") is not None else None
    pinout_verified = 1 if vehicle.get("pinout_verified", 0) else 0

    conn.execute(
        """
        INSERT INTO vehicles (
            car_id, vin_hash, year, model, ecu_family,
            engine_profile_id, transaxle_code, turbo_spec_id, pinout_verified, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(car_id) DO UPDATE SET
            vin_hash = excluded.vin_hash,
            year = excluded.year,
            model = excluded.model,
            ecu_family = excluded.ecu_family,
            engine_profile_id = excluded.engine_profile_id,
            transaxle_code = excluded.transaxle_code,
            turbo_spec_id = excluded.turbo_spec_id,
            pinout_verified = excluded.pinout_verified
        """,
        (
            car_id, vin_hash, int(vehicle["year"]), str(vehicle["model"]),
            str(vehicle["ecu_family"]), str(vehicle["engine_profile_id"]),
            str(vehicle["transaxle_code"]), turbo_spec_id, pinout_verified, created_at,
        ),
    )


def record_profile_revision(conn: sqlite3.Connection, revision: dict[str, Any]) -> tuple[str, bool]:
    """Record a profile revision idempotently by ``(car_id, profile_sha256)``."""
    payload = _normalize_revision_payload(revision)
    existing = conn.execute(
        "SELECT revision_id FROM vehicle_profile_revisions WHERE car_id = ? AND profile_sha256 = ?",
        (payload["car_id"], payload["profile_sha256"]),
    ).fetchone()
    if existing is not None:
        existing_id = existing["revision_id"] if isinstance(existing, sqlite3.Row) else existing[0]
        return str(existing_id), False

    conn.execute(
        """
        INSERT INTO vehicle_profile_revisions (
            revision_id, car_id, schema_version, compatibility_tier,
            profile_sha256, profile_json, swap_checklist_json, compiled_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["revision_id"], payload["car_id"], payload["schema_version"],
            payload["compatibility_tier"], payload["profile_sha256"],
            payload["profile_json"], payload["swap_checklist_json"], payload["compiled_at_utc"],
        ),
    )
    return payload["revision_id"], True


def persist_compiled_profile(
    compiled: dict[str, Any],
    db_path: Path | str = DEFAULT_CATALOG_DB,
) -> dict[str, Any]:
    """Persist a compiled vehicle build profile and revision in one transaction."""
    vehicle_payload = _extract_vehicle_payload(compiled)
    car_id = str(vehicle_payload["car_id"])
    revision_payload = _extract_revision_payload(compiled, car_id)

    conn = connect_catalog(db_path)
    try:
        with conn:
            upsert_vehicle(conn, vehicle_payload)
            revision_id, created = record_profile_revision(conn, revision_payload)
    finally:
        conn.close()

    return {
        "db_path": str(db_path),
        "car_id": car_id,
        "revision_id": revision_id,
        "created": created,
        "profile_sha256": revision_payload["profile_sha256"],
    }


def get_vehicle_and_latest_revision(
    car_id: str,
    db_path: Path | str = DEFAULT_CATALOG_DB,
) -> dict[str, Any] | None:
    """Return a vehicle row and its most recent profile revision, or ``None``."""
    path = Path(db_path)
    if not path.exists():
        return None

    conn = connect_catalog(path)
    try:
        vehicle_row = conn.execute("SELECT * FROM vehicles WHERE car_id = ?", (car_id,)).fetchone()
        if vehicle_row is None:
            return None

        revision_row = conn.execute(
            """
            SELECT * FROM vehicle_profile_revisions
            WHERE car_id = ?
            ORDER BY compiled_at_utc DESC, rowid DESC
            LIMIT 1
            """,
            (car_id,),
        ).fetchone()
        return {
            "vehicle": dict(vehicle_row),
            "latest_revision": dict(revision_row) if revision_row is not None else None,
        }
    finally:
        conn.close()
