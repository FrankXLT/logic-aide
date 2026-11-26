"""WS-2 cleanroom ROM parsers, SCI cross-reference, partitioned manifests, and ctags sync."""

from __future__ import annotations

from logicaide.rom.asm_parsers import (
    parse_asm_file,
    parse_lm_io_pinout_xls,
    parse_lst_file,
    parse_sample_cpp_lm_offsets,
    parse_sym_file,
    parse_tpl_calibration_symbols,
    scrub_cleanroom_text,
)
from logicaide.rom.manifest import (
    DEFAULT_CATALOG_PATH,
    DEFAULT_MANIFESTS_DIR,
    NO_VERIFIED_SOURCE_MATCH,
    ROM_SPECS,
    ROM_SPEC_MAP,
    RomSpec,
    build_partitioned_manifests,
    load_rom_manifest,
    lookup_sci,
    sync_manifests,
)
from logicaide.rom.sci_xref import (
    DEFAULT_XREF_OUTPUT,
    PROTOCOL_COLUMNS,
    build_mpr_sci_crossref,
    load_mpr_sci_crossref,
    lookup_xref_variable,
    parse_mpr_directory,
)

__all__ = [
    "DEFAULT_CATALOG_PATH",
    "DEFAULT_MANIFESTS_DIR",
    "DEFAULT_XREF_OUTPUT",
    "NO_VERIFIED_SOURCE_MATCH",
    "PROTOCOL_COLUMNS",
    "ROM_SPECS",
    "ROM_SPEC_MAP",
    "RomSpec",
    "build_mpr_sci_crossref",
    "build_partitioned_manifests",
    "load_mpr_sci_crossref",
    "load_rom_manifest",
    "lookup_sci",
    "lookup_xref_variable",
    "parse_asm_file",
    "parse_lm_io_pinout_xls",
    "parse_lst_file",
    "parse_mpr_directory",
    "parse_sample_cpp_lm_offsets",
    "parse_sym_file",
    "parse_tpl_calibration_symbols",
    "scrub_cleanroom_text",
    "sync_manifests",
]
