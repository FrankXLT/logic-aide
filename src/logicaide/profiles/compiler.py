"""WS-1 Vehicle Build Profile Compiler for LogicAIDE (M1 / WF-06)."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from jsonschema import Draft202012Validator

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB, persist_compiled_profile

SG_REF: float = 0.742
DEFAULT_REPO_ROOT: Path = Path(__file__).resolve().parents[3]
DEFAULT_SCHEMA_REL = Path("profiles/schema/vehicle_build.schema.json")
DEFAULT_TURBO_CATALOG_REL = Path("profiles/turbo/catalog.json")
DEFAULT_ROM_CATALOG_REL = Path("rom/manifests/catalog.json")
LEGACY_ROM_MANIFEST_REF = "rom/rom_manifest.json"

K_BLOCKS = frozenset({"2.2L_K", "2.5L_K"})
SWAP_BLOCKS = frozenset({"2.4L_EDZ", "2.0L_ECC"})
NATIVE_HEADS = frozenset({"8V_445_BATHTUB", "8V_782_SWIRL", "LOTUS_16V_T3", "MASERATI_16V_TC"})
DOHC_SWAP_HEADS = frozenset({"NEON_ECC_20_DOHC", "ECC_20_DOHC", "EDZ_24_DOHC"})

HYBRID_K_DOHC_MODS: tuple[str, ...] = (
    "oil_coolant_deck_passage_plugging",
    "external_head_oil_feed_and_pan_drainback",
    "head_bolt_stud_sleeve_alignment",
    "custom_timing_belt_tensioner_geometry",
    "piston_valve_reliefs",
    "smec_sbec_crank_cam_hall_sync",
    "ccw_peftbl_cam_peak_rotation",
)
HYBRID_FULL_SWAP_MODS: tuple[str, ...] = (
    "smec_sbec_crank_cam_hall_sync",
    "ccw_peftbl_cam_peak_rotation",
    "edz_chassis_mount_swap",
)
DEFAULT_ROM_IDS = {"SMEC_DUAL_BOARD": "smec_88_89_v19", "SBEC_SINGLE_BOARD": "sbec_90_93_v3_1"}
SWAP_ADVISORY_MAP: dict[str, tuple[str, str]] = {
    "oil_coolant_deck_passage_plugging": ("Plug deck oil/coolant passages", "Tap and plug mismatched K-block and DOHC head deck oil/coolant passages."),
    "external_head_oil_feed_and_pan_drainback": ("External head oil feed & drainback", "Install external oil supply line to DOHC head and dedicated pan drainback."),
    "head_bolt_stud_sleeve_alignment": ("Head-bolt/stud alignment sleeves", "Install stepped alignment sleeves for 11mm K-block fasteners in 10mm head bores."),
    "custom_timing_belt_tensioner_geometry": ("Timing belt & tensioner geometry", "Verify DOHC timing belt length, idler routing, and tensioner clearance."),
    "piston_valve_reliefs": ("Machine piston valve reliefs", "Confirm piston crown valve-pocket clearance (piston_valve_reliefs=true)."),
    "smec_sbec_crank_cam_hall_sync": ("SMEC/SBEC crank & cam Hall sync", "Confirm crank reluctor/Hall and cam sensor sync phasing for SMEC/SBEC."),
    "ccw_peftbl_cam_peak_rotation": ("Rotate PEFTBL for CCW DOHC peak", "Re-index PEFTBL airflow/VE cells for higher-RPM CCW DOHC torque peak."),
    "edz_chassis_mount_swap": ("EDZ/ECC chassis mount swap", "Adapt engine/transaxle mounts and accessory clearances for EDZ/ECC block swap."),
    "10mm_11mm_bolt_gasket_note": ("10mm/11mm bolt & gasket bore match", "Match head gasket fastener holes and torque spec to block bolt diameter."),
}


class ProfileCompilationError(ValueError):
    """Structured error raised when vehicle profile compilation fails a gate."""

    def __init__(self, stage: str, details: list[str] | str) -> None:
        normalized = [details] if isinstance(details, str) else [str(item) for item in details]
        self.stage = str(stage)
        self.details = normalized or ["Unspecified profile compilation failure."]
        super().__init__(f"[{self.stage}] " + "; ".join(self.details))


def _load_json_object(path: Path, stage: str, label: str) -> dict[str, Any]:
    """Read a UTF-8 JSON file and verify its root value is a JSON object."""
    if not path.is_file():
        raise ProfileCompilationError(stage, [f"{label} not found: {path}"])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileCompilationError(stage, [f"Failed to parse {label} '{path}': {exc}"]) from exc
    if not isinstance(payload, dict):
        raise ProfileCompilationError(stage, [f"{label} '{path}' must be a JSON object."])
    return payload


def _resolve_repo_relative_path(raw_ref: Any, repo_root: Path, stage: str, field_name: str) -> Path:
    """Resolve a relative reference safely inside ``repo_root``."""
    if not isinstance(raw_ref, str) or not raw_ref.strip():
        raise ProfileCompilationError(stage, [f"Missing or invalid '{field_name}'."])
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_ref):
        raise ProfileCompilationError(stage, [f"Unsafe '{field_name}' path: control characters are forbidden."])
    if raw_ref.startswith(("/", "\\")) or ":" in raw_ref:
        raise ProfileCompilationError(stage, [f"Unsafe '{field_name}' path '{raw_ref}': must be repo-relative."])
    ref_path = Path(raw_ref)
    if ref_path.is_absolute() or ".." in ref_path.parts:
        raise ProfileCompilationError(stage, [f"Unsafe '{field_name}' path '{raw_ref}': traversal is forbidden."])
    root_resolved = repo_root.resolve()
    candidate = (root_resolved / ref_path).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ProfileCompilationError(stage, [f"Path escape detected in '{field_name}': '{raw_ref}'."]) from exc
    return candidate


def _require_finite_number(
    mapping: dict[str, Any], key: str, stage: str, *, positive: bool = False, non_negative: bool = False
) -> float:
    """Extract and range-check a numeric field (excluding booleans)."""
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileCompilationError(stage, [f"Field '{key}' must be a number."])
    num = float(value)
    if not math.isfinite(num):
        raise ProfileCompilationError(stage, [f"Field '{key}' must be finite."])
    if positive and num <= 0.0:
        raise ProfileCompilationError(stage, [f"Field '{key}' must be > 0 (got {num})."])
    if non_negative and num < 0.0:
        raise ProfileCompilationError(stage, [f"Field '{key}' must be >= 0 (got {num})."])
    return num


def _interp_curve(xs: list[float], ys: list[float], x: float) -> float:
    """Linearly interpolate ``ys`` over strictly ascending ``xs``."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for idx in range(len(xs) - 1):
        if xs[idx] <= x <= xs[idx + 1]:
            span = xs[idx + 1] - xs[idx]
            return ys[idx] + ((x - xs[idx]) / span if span > 0.0 else 0.0) * (ys[idx + 1] - ys[idx])
    return ys[-1]


def validate_profile_schema(profile: dict[str, Any], schema_path: Path | str | None = None) -> None:
    """Validate ``profile`` against Draft 2020-12 ``vehicle_build.schema.json``."""
    resolved_schema = Path(schema_path) if schema_path is not None else DEFAULT_REPO_ROOT / DEFAULT_SCHEMA_REL
    schema = _load_json_object(resolved_schema, "schema", "Vehicle build schema")
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(profile),
        key=lambda err: ([str(part) for part in err.absolute_path], err.message),
    )
    if errors:
        details = [f"{'.'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}" for err in errors]
        raise ProfileCompilationError("schema", details)


def evaluate_mopar_compatibility(profile: dict[str, Any]) -> dict[str, Any]:
    """Classify 3-tier Mopar block/head compatibility and build the swap checklist."""
    bottom = profile.get("engine_bottom_end")
    head = profile.get("cylinder_head_and_cam")
    if not isinstance(bottom, dict) or not isinstance(head, dict):
        raise ProfileCompilationError("compatibility", ["Missing 'engine_bottom_end' or 'cylinder_head_and_cam'."])

    block_code, head_code = bottom.get("block_code"), head.get("head_code")
    declared_tier = head.get("compatibility_tier")
    raw_mods = head.get("required_swap_mods")
    declared_mods = [str(item) for item in raw_mods] if isinstance(raw_mods, list) else []
    piston_valve_reliefs = bottom.get("piston_valve_reliefs")

    if block_code in SWAP_BLOCKS and head_code in NATIVE_HEADS:
        raise ProfileCompilationError("compatibility", [f"INCOMPATIBLE pairing: '{block_code}' cannot use '{head_code}'."])

    if block_code in K_BLOCKS and head_code in NATIVE_HEADS:
        computed_tier, mandatory_mods, require_reliefs = "BOLT_ON_NATIVE", (), False
    elif block_code in K_BLOCKS and head_code in DOHC_SWAP_HEADS:
        computed_tier, mandatory_mods, require_reliefs = "HYBRID_MACHINING_REQUIRED", HYBRID_K_DOHC_MODS, True
    elif block_code in SWAP_BLOCKS and head_code in DOHC_SWAP_HEADS:
        computed_tier, mandatory_mods, require_reliefs = "HYBRID_MACHINING_REQUIRED", HYBRID_FULL_SWAP_MODS, False
    else:
        raise ProfileCompilationError("compatibility", [f"Unsupported pairing: block={block_code!r}, head={head_code!r}."])

    errors: list[str] = []
    if declared_tier != computed_tier:
        errors.append(f"Declared compatibility_tier '{declared_tier}' != computed '{computed_tier}' for {block_code} + {head_code}.")
    if require_reliefs and piston_valve_reliefs is not True:
        errors.append("engine_bottom_end.piston_valve_reliefs must be true for K-block + DOHC hybrid.")
    missing_mods = [mod for mod in mandatory_mods if mod not in declared_mods]
    if missing_mods:
        errors.append("Missing required_swap_mods: " + ", ".join(missing_mods))
    if errors:
        raise ProfileCompilationError("compatibility", errors)

    swap_checklist = [
        {"key": k, "severity": "AMBER", "title": SWAP_ADVISORY_MAP[k][0], "description": SWAP_ADVISORY_MAP[k][1], "verified": True}
        for k in mandatory_mods
    ]
    return {
        "block_code": block_code,
        "head_code": head_code,
        "compatibility_tier": computed_tier,
        "advisory_level": "AMBER" if swap_checklist else "NONE",
        "swap_checklist": swap_checklist,
        "required_swap_mods": list(mandatory_mods),
        "declared_swap_mods": declared_mods,
        "piston_valve_reliefs": bool(piston_valve_reliefs),
    }


def verify_fuel_scaling(
    fuel_system: dict[str, Any], *, abs_tol_lb_hr: float = 0.05, rel_tol: float = 0.005
) -> dict[str, Any]:
    """Verify effective injector flow against rail pressure and specific gravity scaling."""
    if not isinstance(fuel_system, dict):
        raise ProfileCompilationError("fuel_scaling", ["'fuel_system' must be a JSON object."])

    rated_flow = _require_finite_number(fuel_system, "rated_injector_flow_lb_hr", "fuel_scaling", positive=True)
    base_pressure = _require_finite_number(fuel_system, "base_fuel_pressure_psi", "fuel_scaling", positive=True)
    rated_pressure = _require_finite_number(fuel_system, "rated_injector_pressure_psi", "fuel_scaling", positive=True)
    specific_gravity = _require_finite_number(fuel_system, "specific_gravity", "fuel_scaling", positive=True)
    declared_eff = _require_finite_number(fuel_system, "effective_flow_lb_hr", "fuel_scaling", positive=True)

    expected_eff = rated_flow * math.sqrt(base_pressure / rated_pressure) * math.sqrt(specific_gravity / SG_REF)
    tolerance_lb_hr = max(float(abs_tol_lb_hr), float(rel_tol) * expected_eff)
    delta_lb_hr = abs(declared_eff - expected_eff)
    if delta_lb_hr > tolerance_lb_hr:
        raise ProfileCompilationError(
            "fuel_scaling",
            [f"effective_flow_lb_hr={declared_eff:.4f} differs from expected {expected_eff:.4f} lb/hr by {delta_lb_hr:.4f} (tol {tolerance_lb_hr:.4f})."],
        )

    return {
        "verified": True,
        "rated_injector_flow_lb_hr": rated_flow,
        "rated_injector_pressure_psi": rated_pressure,
        "base_fuel_pressure_psi": base_pressure,
        "specific_gravity": specific_gravity,
        "sg_ref": SG_REF,
        "declared_effective_flow_lb_hr": declared_eff,
        "expected_effective_flow_lb_hr": round(expected_eff, 6),
        "delta_lb_hr": round(delta_lb_hr, 6),
        "tolerance_lb_hr": round(tolerance_lb_hr, 6),
    }


def _validate_batoff_curve(batoff: Any) -> tuple[list[float], list[float], float, float, float]:
    """Validate BATOFF deadtime axes, monotonicity, and short-pulse thresholds."""
    if not isinstance(batoff, dict):
        raise ProfileCompilationError("injector_batoff", ["Missing 'batoff_deadtime_ms' object."])
    raw_volts, raw_dts = batoff.get("voltage_points_v"), batoff.get("deadtime_ms")
    if not isinstance(raw_volts, list) or not isinstance(raw_dts, list):
        raise ProfileCompilationError("injector_batoff", ["'voltage_points_v' and 'deadtime_ms' must be arrays."])
    if len(raw_volts) < 2 or len(raw_volts) != len(raw_dts):
        raise ProfileCompilationError("injector_batoff", ["'voltage_points_v' and 'deadtime_ms' must have equal length >= 2."])

    volts: list[float] = []
    deadtimes: list[float] = []
    for v_val, dt_val in zip(raw_volts, raw_dts):
        if isinstance(v_val, bool) or not isinstance(v_val, (int, float)) or not math.isfinite(float(v_val)):
            raise ProfileCompilationError("injector_batoff", ["All 'voltage_points_v' entries must be finite numbers."])
        if isinstance(dt_val, bool) or not isinstance(dt_val, (int, float)) or not math.isfinite(float(dt_val)):
            raise ProfileCompilationError("injector_batoff", ["All 'deadtime_ms' entries must be finite numbers."])
        volts.append(float(v_val))
        deadtimes.append(float(dt_val))

    if volts[0] > 8.0 or volts[-1] < 15.0:
        raise ProfileCompilationError("injector_batoff", [f"'voltage_points_v' must cover <= 8.0V to >= 15.0V (got {volts[0]}..{volts[-1]})."])
    if any(volts[idx] >= volts[idx + 1] for idx in range(len(volts) - 1)):
        raise ProfileCompilationError("injector_batoff", ["'voltage_points_v' must be strictly ascending."])
    if any(dt <= 0.0 for dt in deadtimes):
        raise ProfileCompilationError("injector_batoff", ["All 'deadtime_ms' values must be positive."])
    if any(deadtimes[idx] < deadtimes[idx + 1] for idx in range(len(deadtimes) - 1)):
        raise ProfileCompilationError("injector_batoff", ["'deadtime_ms' must be non-increasing."])

    slope = _require_finite_number(batoff, "pressure_slope_ms_per_psi", "injector_batoff", non_negative=True)
    knee = _require_finite_number(batoff, "short_pulse_knee_ms", "injector_batoff", non_negative=True)
    min_linear = _require_finite_number(batoff, "min_linear_pw_ms", "injector_batoff", non_negative=True)
    if min_linear < knee:
        raise ProfileCompilationError("injector_batoff", [f"min_linear_pw_ms ({min_linear}) must be >= short_pulse_knee_ms ({knee})."])
    return volts, deadtimes, slope, knee, min_linear


def crosscheck_injector_batoff(fuel_system: dict[str, Any], presets_path: Path | str) -> dict[str, Any]:
    """Validate injector BATOFF deadtime curve and record nearest/exact preset metadata."""
    if not isinstance(fuel_system, dict):
        raise ProfileCompilationError("injector_batoff", ["'fuel_system' must be a JSON object."])
    volts, deadtimes, slope, knee, min_linear = _validate_batoff_curve(fuel_system.get("batoff_deadtime_ms"))
    catalog = _load_json_object(Path(presets_path), "injector_batoff", "Injector BATOFF presets")
    presets = catalog.get("presets")
    if not isinstance(presets, list):
        raise ProfileCompilationError("injector_batoff", ["Presets file missing 'presets' list."])

    raw_flow, raw_press = fuel_system.get("rated_injector_flow_lb_hr"), fuel_system.get("rated_injector_pressure_psi")
    rated_flow = float(raw_flow) if not isinstance(raw_flow, bool) and isinstance(raw_flow, (int, float)) else None
    rated_press = float(raw_press) if not isinstance(raw_press, bool) and isinstance(raw_press, (int, float)) else None
    best_score: tuple[float, float, float] | None = None
    best_exact_score: tuple[float, float, float] | None = None
    best_meta: dict[str, Any] | None = None
    exact_match_id: str | None = None

    for preset in presets:
        if not isinstance(preset, dict) or not isinstance(preset.get("batoff_deadtime_ms"), dict):
            continue
        pb = preset["batoff_deadtime_ms"]
        p_volts, p_dts = pb.get("voltage_points_v") or [], pb.get("deadtime_ms") or []
        if len(p_volts) < 2 or len(p_volts) != len(p_dts):
            continue
        px, py = [float(v) for v in p_volts], [float(d) for d in p_dts]
        diffs = [abs(dt - _interp_curve(px, py, v)) for v, dt in zip(volts, deadtimes) if px[0] <= v <= px[-1]]
        if not diffs:
            continue
        rmse, max_diff = math.sqrt(sum(d * d for d in diffs) / len(diffs)), max(diffs)
        p_flow, p_press = preset.get("rated_flow_lb_hr"), preset.get("rated_pressure_psi")
        flow_diff = abs(float(p_flow) - rated_flow) if (rated_flow is not None and p_flow is not None) else 0.0
        press_diff = abs(float(p_press) - rated_press) if (rated_press is not None and p_press is not None) else 0.0
        score = (round(rmse, 9), flow_diff, press_diff)
        if best_score is None or score < best_score:
            best_score = score
            best_meta = {
                "preset_id": preset.get("preset_id"), "label": preset.get("label"),
                "rated_flow_lb_hr": p_flow, "curve_rmse_ms": round(rmse, 6), "max_abs_diff_ms": round(max_diff, 6),
            }
        is_exact = (
            (flow_diff <= 0.15 and press_diff <= 0.5 and rmse <= 0.05)
            if (rated_flow is not None and p_flow is not None and rated_press is not None and p_press is not None)
            else (max_diff <= 1e-6 and volts[0] >= px[0] and volts[-1] <= px[-1])
        )
        if is_exact and (best_exact_score is None or score < best_exact_score):
            best_exact_score, exact_match_id = score, str(preset.get("preset_id"))

    return {
        "curve_valid": True, "voltage_min_v": volts[0], "voltage_max_v": volts[-1], "point_count": len(volts),
        "pressure_slope_ms_per_psi": slope, "short_pulse_knee_ms": knee, "min_linear_pw_ms": min_linear,
        "is_custom_curve": exact_match_id is None, "exact_preset_match": exact_match_id, "nearest_preset": best_meta,
    }


def resolve_turbo_spec(
    turbo_section: dict[str, Any], repo_root: Path, catalog_path: Path | str | None = None
) -> dict[str, Any]:
    """Safely resolve ``turbo_spec_ref`` and cross-check against ``profiles/turbo/catalog.json``."""
    if not isinstance(turbo_section, dict):
        raise ProfileCompilationError("turbo", ["'turbo_and_intercooler' must be a JSON object."])
    turbo_id = turbo_section.get("turbo_id")
    if not isinstance(turbo_id, str) or not turbo_id.strip():
        raise ProfileCompilationError("turbo", ["Missing or invalid 'turbo_id'."])

    root_resolved = Path(repo_root).resolve()
    spec_path = _resolve_repo_relative_path(turbo_section.get("turbo_spec_ref"), root_resolved, "turbo", "turbo_spec_ref")
    spec = _load_json_object(spec_path, "turbo", "Turbo spec")
    if spec.get("turbo_id") != turbo_id:
        raise ProfileCompilationError("turbo", [f"Turbo spec turbo_id {spec.get('turbo_id')!r} != '{turbo_id}'."])

    cat_path = Path(catalog_path) if catalog_path is not None else root_resolved / DEFAULT_TURBO_CATALOG_REL
    catalog = _load_json_object(cat_path, "turbo", "Turbo catalog")
    aliases = catalog.get("aliases") or {}
    entries_by_id = {str(e["turbo_id"]): e for e in (catalog.get("entries") or []) if isinstance(e, dict) and e.get("turbo_id")}
    canonical_id = str(aliases.get(turbo_id, turbo_id))
    catalog_entry = entries_by_id.get(canonical_id)
    if catalog_entry is None:
        raise ProfileCompilationError("turbo", [f"Turbo '{turbo_id}' (canonical '{canonical_id}') not in catalog."])

    spec_sha, cat_sha = spec.get("figure_sha256"), catalog_entry.get("figure_sha256")
    if spec_sha and cat_sha and spec_sha != cat_sha:
        raise ProfileCompilationError("turbo", [f"Turbo '{turbo_id}' figure_sha256 mismatch with '{canonical_id}'."])

    return {
        "turbo_id": turbo_id,
        "canonical_turbo_id": canonical_id,
        "is_alias": turbo_id != canonical_id or bool(spec.get("is_alias", False)),
        "turbo_spec_ref": str(turbo_section["turbo_spec_ref"]),
        "resolved_spec_path": spec_path.relative_to(root_resolved).as_posix(),
        "calibrated": bool(spec.get("calibrated", catalog_entry.get("calibrated", False))),
        "axis_calibration_pending": bool(spec.get("axis_calibration_pending", catalog_entry.get("axis_calibration_pending", False))),
        "figure_sha256": spec_sha or cat_sha,
        "company_name": spec.get("company_name") or catalog_entry.get("company_name"),
        "family_name": spec.get("family_name") or catalog_entry.get("family_name"),
        "product_name": spec.get("product_name") or catalog_entry.get("product_name"),
    }


def _verify_manifest_fields(manifest: dict[str, Any], ecu_family: str, eprom_size_bytes: int, label: str) -> None:
    """Enforce strict ``ecu_family`` and ``rom_size_bytes`` parity on a ROM manifest."""
    m_family, m_size = manifest.get("ecu_family"), manifest.get("rom_size_bytes", manifest.get("eprom_size_bytes"))
    if m_family != ecu_family or m_size != eprom_size_bytes:
        raise ProfileCompilationError(
            "rom_manifest",
            [f"ROM manifest {label} mismatch: expected ({ecu_family!r}, {eprom_size_bytes}), got ({m_family!r}, {m_size!r})."],
        )


def resolve_rom_manifest(
    ecu_section: dict[str, Any], repo_root: Path, *, require_rom_manifest: bool = True
) -> dict[str, Any]:
    """Resolve ``rom_manifest_ref`` directly or via ``rom/manifests/catalog.json``."""
    if not isinstance(ecu_section, dict):
        raise ProfileCompilationError("rom_manifest", ["'ecu_and_sensors' must be a JSON object."])
    ecu_family, eprom_size = ecu_section.get("ecu_family"), ecu_section.get("eprom_size_bytes")
    if not isinstance(ecu_family, str) or not ecu_family.strip():
        raise ProfileCompilationError("rom_manifest", ["Missing or invalid 'ecu_family'."])
    if isinstance(eprom_size, bool) or not isinstance(eprom_size, int) or eprom_size <= 0:
        raise ProfileCompilationError("rom_manifest", ["'eprom_size_bytes' must be a positive integer."])

    root_resolved = Path(repo_root).resolve()
    raw_ref = ecu_section.get("rom_manifest_ref")
    explicit_path = _resolve_repo_relative_path(raw_ref, root_resolved, "rom_manifest", "rom_manifest_ref")
    normalized_ref = Path(str(raw_ref)).as_posix()

    if explicit_path.is_file():
        manifest = _load_json_object(explicit_path, "rom_manifest", "ROM manifest")
        _verify_manifest_fields(manifest, ecu_family, eprom_size, normalized_ref)
        return {
            "resolved": True, "resolved_via": "explicit_file", "rom_id": manifest.get("rom_id"),
            "ecu_family": ecu_family, "rom_size_bytes": eprom_size, "mcu": manifest.get("mcu"),
            "base_address": manifest.get("base_address"), "rom_manifest_ref": str(raw_ref),
            "manifest_ref": explicit_path.relative_to(root_resolved).as_posix(),
        }

    catalog_path = root_resolved / DEFAULT_ROM_CATALOG_REL
    if normalized_ref == LEGACY_ROM_MANIFEST_REF and catalog_path.is_file():
        catalog = _load_json_object(catalog_path, "rom_manifest", "ROM manifest catalog")
        entries = catalog.get("manifests")
        if not isinstance(entries, list):
            raise ProfileCompilationError("rom_manifest", ["ROM catalog missing 'manifests' list."])
        matches = [e for e in entries if isinstance(e, dict) and e.get("ecu_family") == ecu_family and e.get("rom_size_bytes") == eprom_size]
        if not matches:
            raise ProfileCompilationError("rom_manifest", [f"No ROM catalog entry matches ({ecu_family!r}, {eprom_size})."])
        selected = next((m for m in matches if m.get("rom_id") == DEFAULT_ROM_IDS.get(ecu_family)), matches[0])
        part_ref = selected.get("manifest_ref")
        part_path = _resolve_repo_relative_path(part_ref, root_resolved, "rom_manifest", "catalog.manifest_ref") if part_ref else None
        if part_path is not None and part_path.is_file():
            _verify_manifest_fields(_load_json_object(part_path, "rom_manifest", "Partitioned ROM manifest"), ecu_family, eprom_size, str(part_ref))
        elif require_rom_manifest:
            raise ProfileCompilationError("rom_manifest", [f"Partitioned ROM manifest missing for '{selected.get('rom_id')}': {part_ref!r}."])
        return {
            "resolved": bool(part_path and part_path.is_file()), "resolved_via": "partitioned_catalog_bridge",
            "rom_id": selected.get("rom_id"), "ecu_family": ecu_family, "rom_size_bytes": eprom_size,
            "mcu": selected.get("mcu"), "base_address": selected.get("base_address"),
            "rom_manifest_ref": str(raw_ref), "manifest_ref": part_ref,
        }

    if not require_rom_manifest:
        return {
            "resolved": False, "resolved_via": "deferred_missing", "rom_id": None,
            "ecu_family": ecu_family, "rom_size_bytes": eprom_size, "rom_manifest_ref": str(raw_ref), "manifest_ref": None,
        }
    msg = (
        f"Explicit ROM manifest '{raw_ref}' does not exist and non-legacy paths cannot fall back to catalog."
        if normalized_ref != LEGACY_ROM_MANIFEST_REF
        else f"ROM manifest '{raw_ref}' not found and partitioned catalog '{DEFAULT_ROM_CATALOG_REL}' is absent."
    )
    raise ProfileCompilationError("rom_manifest", [msg])


def compile_profile(
    profile_path: Path | str,
    *,
    repo_root: Path | str | None = None,
    db_path: Path | str | None = None,
    persist: bool = True,
    catalog_db_path: Path | str | None = None,
    require_rom_manifest: bool = True,
) -> dict[str, Any]:
    """Compile, gate-check, and optionally persist a LogicAIDE vehicle build profile."""
    resolved_root = Path(repo_root).resolve() if repo_root is not None else DEFAULT_REPO_ROOT
    if db_path is not None and catalog_db_path is not None and Path(db_path) != Path(catalog_db_path):
        raise ProfileCompilationError("catalog", ["Conflicting 'db_path' and 'catalog_db_path' arguments supplied."])
    effective_db = Path(db_path if db_path is not None else (catalog_db_path or resolved_root / DEFAULT_CATALOG_DB))

    profile = _load_json_object(Path(profile_path), "load", "Vehicle build profile")
    canonical_json = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    profile_sha256 = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    validate_profile_schema(profile, schema_path=resolved_root / DEFAULT_SCHEMA_REL)
    compat = evaluate_mopar_compatibility(profile)
    fuel_scaling = verify_fuel_scaling(profile["fuel_system"])
    injector_batoff = crosscheck_injector_batoff(profile["fuel_system"], resolved_root / "profiles/presets/injectors_batoff.json")
    turbo = resolve_turbo_spec(profile["turbo_and_intercooler"], resolved_root, catalog_path=resolved_root / DEFAULT_TURBO_CATALOG_REL)
    rom_manifest = resolve_rom_manifest(profile["ecu_and_sensors"], resolved_root, require_rom_manifest=require_rom_manifest)

    report: dict[str, Any] = {
        "car_id": str(profile["identity_and_chassis"]["car_id"]), "profile_sha256": profile_sha256,
        "compatibility_tier": compat["compatibility_tier"], "swap_checklist": compat["swap_checklist"],
        "fuel_scaling": fuel_scaling, "injector_batoff": injector_batoff,
        "turbo": turbo, "rom_manifest": rom_manifest, "catalog_record": None,
    }
    if persist:
        try:
            report["catalog_record"] = persist_compiled_profile(
                {**report, "profile": profile, "profile_json": canonical_json}, db_path=effective_db
            )
        except (sqlite3.Error, OSError, KeyError, ValueError) as exc:
            raise ProfileCompilationError("catalog", [f"Catalog persistence failed: {exc}"]) from exc
    return report
