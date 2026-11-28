# Data Model, Multi-Car Parquet Lake & CLI/FastMCP Specification

LogicAIDE normalizes road telemetry from heterogeneous 1988–89 **SMEC** and 1990–93 **SBEC** vehicles alongside deterministic `m68hc11` single-replays and Monte Carlo sweeps into one unified offline lake (`data/drives/`). Storage splits cleanly into a **SQLite relational catalog**, **Hive-partitioned 30-second Zstandard PyArrow Parquet chunks**, and **DuckDB analytical rollups**.

---

## 1. SQLite Catalog DDL (`data/drives/catalog.sqlite`)

All paths in `parquet_chunks` and `stimulus_fixtures` are project-relative; raw GPS coordinates are stripped before fixture or staging persistence (`CLAUDE.md`). Partition keys (`run_kind`, `car_id`, `build_id`, `session_id`) match `.claude/rules/telemetry-storage.md` and DuckDB queries end-to-end.

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE vehicles (
    car_id             TEXT PRIMARY KEY,
    vin_hash           TEXT UNIQUE,
    year               INTEGER NOT NULL CHECK (year BETWEEN 1984 AND 1995),
    model              TEXT NOT NULL,
    ecu_family         TEXT NOT NULL CHECK (ecu_family IN ('LM_PM', 'SMEC_DUAL_BOARD', 'SBEC_SINGLE_BOARD')),
    engine_profile_id  TEXT NOT NULL,
    transaxle_code     TEXT NOT NULL,
    turbo_spec_id      TEXT,
    pinout_verified    INTEGER NOT NULL DEFAULT 0 CHECK (pinout_verified IN (0, 1)),
    created_at         TEXT NOT NULL
);

CREATE TABLE vehicle_profile_revisions (
    revision_id         TEXT PRIMARY KEY,
    car_id              TEXT NOT NULL REFERENCES vehicles(car_id),
    schema_version      TEXT NOT NULL DEFAULT '2020-12',
    compatibility_tier  TEXT NOT NULL CHECK (compatibility_tier IN ('BOLT_ON_NATIVE', 'HYBRID_MACHINING_REQUIRED')),
    profile_sha256      TEXT NOT NULL,
    profile_json        TEXT NOT NULL,
    swap_checklist_json TEXT NOT NULL DEFAULT '[]',
    compiled_at_utc     TEXT NOT NULL
);

CREATE TABLE sessions (
    session_id             TEXT PRIMARY KEY,
    car_id                 TEXT NOT NULL REFERENCES vehicles(car_id),
    build_id               TEXT NOT NULL DEFAULT 'STOCK',
    started_at_utc         TEXT NOT NULL,
    ended_at_utc           TEXT,
    run_kind               TEXT NOT NULL CHECK (run_kind IN ('VEHICLE_SCI', 'SINGLE_REPLAY', 'MONTE_CARLO_SWEEP')),
    rom_sha256             TEXT NOT NULL,
    baro_kpa               REAL NOT NULL DEFAULT 101.325,
    quarantined_cells_json TEXT NOT NULL DEFAULT '[]',
    imu_attached           INTEGER NOT NULL DEFAULT 0 CHECK (imu_attached IN (0, 1)),
    gps_stripped           INTEGER NOT NULL DEFAULT 1 CHECK (gps_stripped IN (0, 1)),
    notes                  TEXT
);

CREATE TABLE bookmarks (
    bookmark_id  TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    ts_utc_ns    INTEGER NOT NULL,
    sample_idx   INTEGER NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('SPACEBAR', 'TOUCH', 'AUTO_ANOMALY')),
    label        TEXT NOT NULL,
    note         TEXT
);

CREATE TABLE tuning_issues (
    issue_id     TEXT PRIMARY KEY,
    car_id       TEXT NOT NULL REFERENCES vehicles(car_id),
    session_id   TEXT REFERENCES sessions(session_id),
    build_id     TEXT,
    campaign_id  TEXT,
    severity     TEXT NOT NULL CHECK (severity IN ('INFO', 'CAUTION', 'CRITICAL')),
    category     TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('OPEN', 'IN_PROGRESS', 'VERIFIED_FIXED', 'WONT_FIX')),
    title        TEXT NOT NULL,
    description  TEXT NOT NULL,
    rpm_min      REAL,
    rpm_max      REAL,
    map_min_kpa  REAL,
    map_max_kpa  REAL,
    created_at   TEXT NOT NULL
);

CREATE TABLE optimization_campaigns (
    campaign_id          TEXT PRIMARY KEY,
    car_id               TEXT NOT NULL REFERENCES vehicles(car_id),
    baseline_rom_sha256  TEXT NOT NULL,
    issue_id             TEXT REFERENCES tuning_issues(issue_id),
    sampler              TEXT NOT NULL CHECK (sampler IN ('SOBOL', 'LHS', 'OPTUNA')),
    n_trials             INTEGER NOT NULL CHECK (n_trials > 0),
    status               TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    objective_summary    TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE parquet_chunks (
    chunk_id     TEXT PRIMARY KEY,
    session_id   TEXT REFERENCES sessions(session_id),
    car_id       TEXT NOT NULL REFERENCES vehicles(car_id),
    build_id     TEXT NOT NULL DEFAULT 'STOCK',
    campaign_id  TEXT REFERENCES optimization_campaigns(campaign_id),
    run_kind     TEXT NOT NULL CHECK (run_kind IN ('VEHICLE_SCI', 'SINGLE_REPLAY', 'MONTE_CARLO_SWEEP')),
    file_path    TEXT UNIQUE NOT NULL,
    row_count    INTEGER NOT NULL CHECK (row_count >= 0),
    t_start_ns   INTEGER NOT NULL,
    t_end_ns     INTEGER NOT NULL,
    rpm_min      REAL,
    rpm_max      REAL,
    map_min_kpa  REAL,
    map_max_kpa  REAL,
    zstd_level   INTEGER NOT NULL DEFAULT 3,
    sha256       TEXT NOT NULL
);

CREATE TABLE stimulus_fixtures (
    fixture_id         TEXT PRIMARY KEY,
    car_id             TEXT NOT NULL REFERENCES vehicles(car_id),
    source_session_id  TEXT REFERENCES sessions(session_id),
    issue_id           TEXT REFERENCES tuning_issues(issue_id),
    file_path          TEXT UNIQUE NOT NULL,
    gps_stripped       INTEGER NOT NULL DEFAULT 1 CHECK (gps_stripped = 1),
    sha256             TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE TABLE candidate_builds (
    build_id            TEXT PRIMARY KEY,
    car_id              TEXT NOT NULL REFERENCES vehicles(car_id),
    campaign_id         TEXT REFERENCES optimization_campaigns(campaign_id),
    baseline_sha256     TEXT NOT NULL,
    candidate_sha256    TEXT NOT NULL,
    staging_dir         TEXT UNIQUE NOT NULL,
    vectors_ok          INTEGER NOT NULL CHECK (vectors_ok IN (0, 1)),
    max_loop_cycles     INTEGER NOT NULL,
    twin_parity_ok      INTEGER NOT NULL CHECK (twin_parity_ok IN (0, 1)),
    sweep_overflows     INTEGER NOT NULL,
    safety_gate_status  TEXT NOT NULL CHECK (safety_gate_status IN ('PASS', 'BLOCKED')),
    created_at          TEXT NOT NULL
);
```

---

## 2. Unified 30s Zstandard PyArrow Parquet Contract

Live SCI captures, `m68hc11` single-replays, and Sobol/Optuna sweep outputs share **one canonical PyArrow schema** rotated into 30-second Hive-partitioned chunk files (`compression="zstd"`, `compression_level=3`, Parquet statistics enabled on `rpm`, `map_kpa`, `tps_pct`, and `act_c`):

```text
data/drives/run_kind=<run_kind>/car_id=<car_id>/build_id=<build_id>/session_id=<session_id>/chunk_<nnnn>.parquet
```

### 2.1 Nullability & Unit Discipline
- **Explicit Nullability (`null` $\neq$ `0`)**: Any SCI cell that was unpolled, unsupported on the ECU mask, or quarantined by Dead-Cell Timeout Quarantine MUST remain **`null`**, never coerced to `0` or `0.0`.
- **Dead-Cell Timeout Quarantine (`dead_cell_quarantine`)**: Frame timeouts, checksum errors, or SCI bus stalls tag affected intervals with `dead_cell_quarantine = true` so statistical views skip stalled intervals cleanly.
- **`NIST SP 811` Quantities**: Scaled engineering columns follow standard SI/engineering units (`min^-1`, `kPa`, `psi`, `%`, `ms`, `deg BTDC`, `°C`, `V`).
- **Privacy Guarantee**: Optional phone IMU merges (`imu_grade_pct`, `imu_lon_accel_mps2`, `imu_lat_accel_mps2`) drop all raw `lat`/`lon`/`altitude`/`gps_track` columns prior to writing exported Parquet fixtures.

### 2.2 Canonical Column Schema

| Column Name | Arrow Type | Nullable | Description / Units |
| :--- | :--- | :---: | :--- |
| `ts_utc_ns` | `int64` | No | Monotonic UTC timestamp in nanoseconds (`ns`) or replay clock offset. |
| `session_id` | `string` | No | Foreign key to `sessions.session_id` (Hive partition key). |
| `car_id` | `string` | No | Foreign key to `vehicles.car_id` (Hive partition key). |
| `build_id` | `string` | No | Candidate ROM `build_id` or `'STOCK'` (Hive partition key). |
| `run_kind` | `string` | No | Enum (`VEHICLE_SCI` \| `SINGLE_REPLAY` \| `MONTE_CARLO_SWEEP`; Hive partition key). |
| `campaign_id` | `string` | Yes | Linked `optimization_campaigns.campaign_id` (non-null for sweeps). |
| `issue_id` | `string` | Yes | Linked `tuning_issues.issue_id` if anomaly/regression flagged. |
| `trial_idx` | `int32` | Yes | Monte Carlo trial index (`null` for road drives). |
| `dead_cell_quarantine` | `bool` | No | `true` when SCI timeout, checksum mismatch, or bus stall invalidates sample; default `false`. |
| `raw_rpm_b` | `uint8` | Yes | Raw `0xF2`/`0xF4` SCI byte (`uint8`) for RPM. |
| `raw_map_b` | `uint8` | Yes | Raw SCI MAP ADC/scaled byte (`uint8`). |
| `raw_tps_b` | `uint8` | Yes | Raw SCI TPS byte (`uint8`). |
| `raw_pw_u16` | `uint16` | Yes | Raw injector pulse width register ticks (`uint16`). |
| `raw_spark_b` | `uint8` | Yes | Raw SCI spark advance byte (`uint8`). |
| `raw_act_b` | `uint8` | Yes | Raw SCI intake air charge temp byte (`uint8`). |
| `raw_cts_b` | `uint8` | Yes | Raw SCI coolant temp byte (`uint8`). |
| `raw_batt_b` | `uint8` | Yes | Raw SCI battery voltage byte (`uint8`). |
| `raw_o2_b` | `uint8` | Yes | Raw SCI narrowband O2 byte (`uint8`). |
| `raw_knock_b` | `uint8` | Yes | Raw SCI knock counter / retard byte (`uint8`). |
| `rpm` | `float32` | Yes | Engine speed / `min⁻¹` (`RPM`). |
| `map_kpa` | `float32` | Yes | Manifold Absolute Pressure / `kPa`. |
| `boost_psi` | `float32` | Yes | Gauge boost / `psi` (`(map_kpa - baro_kpa) * 0.1450377`). |
| `tps_pct` | `float32` | Yes | Throttle Position / `%` (`0.0–100.0`). |
| `pw_ms` | `float32` | Yes | Injector Pulse Width / `ms`. |
| `spark_deg_btdc` | `float32` | Yes | Ignition Advance / `deg BTDC`. |
| `act_c` | `float32` | Yes | Intake Charge Temp / `°C`. |
| `cts_c` | `float32` | Yes | Coolant Temp / `°C`. |
| `batt_v` | `float32` | Yes | Battery Bus Voltage / `V`. |
| `afr` | `float32` | Yes | Air-Fuel Ratio (wideband merge or twin-computed; `null` if narrowband-only). |
| `knock_counts` | `uint16` | Yes | Decoded knock events (`null` when unpolled / `Not in Watchlist (null)`). |
| `imu_grade_pct` | `float32` | Yes | Optional GPS-stripped phone IMU road grade / `%`. |
| `imu_lon_accel_mps2` | `float32` | Yes | Optional GPS-stripped longitudinal acceleration / `m·s⁻²`. |
| `imu_lat_accel_mps2` | `float32` | Yes | Optional GPS-stripped lateral acceleration / `m·s⁻²`. |

---

## 3. `load_cell_rollups` Data Lake (DuckDB)

DuckDB aggregates multi-car road pulls (`VEHICLE_SCI`), single-replays (`SINGLE_REPLAY`), and synthetic sweeps (`MONTE_CARLO_SWEEP`) into canonical **`250 RPM x 10 kPa MAP x TPS Regime x Charge-Temp`** load-cell bins while excluding quarantined ticks (`NOT dead_cell_quarantine`) and preserving SQL `NULL` when optional channels like `knock_counts` or `afr` were never polled (`null != 0`):

```sql
CREATE OR REPLACE VIEW load_cell_rollups AS
SELECT
    car_id,
    build_id,
    session_id,
    run_kind,
    campaign_id,
    CAST(FLOOR(rpm / 250.0) * 250 AS INTEGER)     AS rpm_bin,
    CAST(FLOOR(map_kpa / 10.0) * 10 AS INTEGER)   AS map_bin_kpa,
    CASE
        WHEN tps_pct IS NULL THEN 'UNPOLLED'
        WHEN tps_pct < 3.0   THEN 'IDLE'
        WHEN tps_pct < 35.0  THEN 'PART_THROTTLE'
        WHEN tps_pct < 75.0  THEN 'TRANSITION'
        ELSE 'WOT'
    END                                           AS tps_regime,
    CASE
        WHEN act_c IS NULL   THEN 'UNPOLLED'
        WHEN act_c < 20.0    THEN 'COLD_<20C'
        WHEN act_c < 40.0    THEN 'WARM_20_40C'
        WHEN act_c < 60.0    THEN 'HOT_40_60C'
        ELSE 'HEATSOAK_>=60C'
    END                                           AS charge_temp_bin,
    COUNT(*)                                      AS sample_count,
    AVG(pw_ms)                                    AS mean_pw_ms,
    AVG(spark_deg_btdc)                           AS mean_spark_deg,
    AVG(afr)                                      AS mean_afr,
    SUM(knock_counts)                             AS total_knock_events,
    COUNT(knock_counts)                           AS knock_polled_samples,
    MIN(batt_v)                                   AS min_batt_v
FROM read_parquet('data/drives/**/*.parquet', hive_partitioning = true)
WHERE rpm IS NOT NULL
  AND map_kpa IS NOT NULL
  AND NOT dead_cell_quarantine
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9;
```

---

## 4. CLI-First (`uv run logicaide ...`) & FastMCP Tool Contracts

Every core subsystem exposes identical deterministic logic through both `uv run logicaide` CLI commands and read/simulate-only **FastMCP** servers under `mcp_servers/`.

| Subsystem | CLI Command (`uv run logicaide ...`) | FastMCP Server (`mcp_servers/`) | Exposed Operations |
| :--- | :--- | :--- | :--- |
| **Vehicle Profiles** | `profile compile` | *(CLI & SQLite Catalog)* | Validate Draft 2020-12 build JSON, enforce Mopar 3-tier compatibility, persist `vehicles` & `vehicle_profile_revisions` |
| **Telemetry & Lake** | `telemetry watch`, `telemetry ingest`, `telemetry rollups`, `telemetry issues`, `telemetry export-fixture` | **`ecu-telemetry`** *(6 tools)* | `list_vehicles`, `query_load_cell_rollups`, `get_drive_timeseries`, `list_bookmarks`, `create_or_update_tuning_issue`, `export_gps_stripped_fixture` |
| **Emulator & Twin** | `sim splay`, `sim manifest-sync`, `sim verify-sha256`, `sim parity`, `sim replay`, `sim sweep`, `sim stage` | **`hc11-emulator`** *(6 tools)* | `inspect_rom_manifest`, `verify_roundtrip_sha256`, `run_twin_parity`, `run_single_replay`, `run_monte_carlo_sweep`, `evaluate_staging_safety_gate` |
| **Heritage RAG** | `heritage crawl`, `heritage parse-web`, `heritage parse-docs`, `heritage build-index`, `heritage query`, `heritage diagram`, `heritage discover`, `heritage sync-wiki` | **`chrysler-heritage`** *(7 tools)* | `search_heritage_docs`, `get_verified_schematic`, `lookup_60way_pin`, `lookup_sci_offset`, `get_provenance_card`, `discover_heritage_leads`, `sync_heritage_wiki` |
| **Workbench & Briefs** | `readiness`, `brief session`, `brief cell`, `brief issue`, `brief build` | *(CLI & `.mcp.json` Bridge)* | Audit `catalog.sqlite`, Parquet `load_cell_rollups`, ROM manifests, and `data/heritage_index.sqlite` freshness (`--auto-rebuild`); emit GPS/PII-free Agent Briefs with MCP follow-up hints |

### Zero Direct Serial-Flash Tool Exposure & Manifest-Only SCI Polling
- **No Flashing or Raw Port Tools**: Neither `ecu-telemetry`, `hc11-emulator`, `chrysler-heritage`, nor any agent-invocable CLI wrapper exposes `ostrich2`, `flash_ecu`, `boostbutton`, `mptun`, `minipro`, `xgpro`, `stm32flash`, Pin 11 bootstrap activation, or physical `/dev/ttyUSB*` / `COM*` write handles (`telemetry watch` is strictly read-only SCI polling, with `pyserial` `loop://` allowed for offline tests run via `uv run pytest -n 0`).
- **Manifest-Only `0xF2`/`0xF4` Resolution**: `telemetry watch` never hardcodes `0xF2`/`0xF4` RAM offsets across SMEC vs. SBEC calibration masks — every offset comes strictly from `rom_manifest.json`.
- **Staging Boundary Only**: `hc11-emulator` writes validated candidate builds and `REPORT.md` safety audits exclusively to `staging/candidates/<build_id>/` and emits a copyable manual flash CLI block only when all safety gate checks pass; `brief build` mirrors this Hardware Deadbolt by emitting the Pin 11 continuity reminder and `minipro` command only on `PASS` and suppressing programmer commands when `BLOCKED`.
- **Root `.mcp.json` & `--stdio` Discovery**: Root `.mcp.json` activates all three read/simulate-only servers (`ecu-telemetry` 6 tools, `hc11-emulator` 6 tools, and `chrysler-heritage` 7 tools including `discover_heritage_leads` and `sync_heritage_wiki`) via `uv run --offline python mcp_servers/<server>.py --stdio`, supporting optional `FastMCP` registration and built-in zero-dependency JSON-RPC `--stdio`.
