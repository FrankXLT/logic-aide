"""M3 cleanroom 68HC11 assembler, splay/roundtrip, emulator, Monte Carlo, and safety gate."""

from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from logicaide.sim.assembler import (
    AssembledRom,
    AssemblyError,
    assemble_source,
    assemble_tree,
    detect_external_assembler,
    format_s19,
)
from logicaide.sim.cli_sim import register_sim_commands
from logicaide.sim.hc11_runner import (
    HC11_CLOCK_HZ,
    MAX_MAIN_LOOP_CYCLES,
    MAX_MAIN_LOOP_MS,
    LoopTimingResult,
    TwinParityCase,
    TwinParitySummary,
    VectorCheckResult,
    create_hc11_cpu,
    execute_ram_case,
    measure_main_loop_budget,
    run_twin_parity,
    validate_rom_vectors,
)
from logicaide.sim.monte_carlo import (
    BAR_OVERBOOST_CEILINGS,
    MC_SAMPLERS,
    REV_LIMIT_CEILING_RPM,
    SweepSummary,
    run_monte_carlo_sweep,
)
from logicaide.sim.replay import SingleReplaySummary, run_single_replay
from logicaide.sim.safety_gate import (
    SafetyCheckItem,
    StagingGateResult,
    run_staging_safety_gate,
)
from logicaide.sim.splay import (
    RoundtripVerificationResult,
    SplayedTree,
    build_candidate_rom_bytes,
    ensure_writable_sim_path,
    extract_calibrations_from_rom,
    splay_rom_binary,
    verify_roundtrip_sha256,
)

__all__ = [
    "AssembledRom", "AssemblyError", "BAR_OVERBOOST_CEILINGS", "HC11_CLOCK_HZ",
    "LoopTimingResult", "MAX_MAIN_LOOP_CYCLES", "MAX_MAIN_LOOP_MS", "MC_SAMPLERS",
    "REV_LIMIT_CEILING_RPM", "RoundtripVerificationResult", "SafetyCheckItem",
    "SingleReplaySummary", "SplayedTree", "StagingGateResult", "SweepSummary",
    "TwinParityCase", "TwinParitySummary", "VectorCheckResult", "assemble_source",
    "assemble_tree", "build_candidate_rom_bytes", "create_hc11_cpu",
    "detect_external_assembler", "ensure_writable_sim_path", "execute_ram_case",
    "extract_calibrations_from_rom", "format_s19", "measure_main_loop_budget",
    "register_sim_commands", "run_monte_carlo_sweep", "run_single_replay",
    "run_staging_safety_gate", "run_twin_parity", "splay_rom_binary",
    "validate_rom_vectors", "verify_roundtrip_sha256",
]
