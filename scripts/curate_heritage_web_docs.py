#!/usr/bin/env python3
"""CLI wrapper to extract Wave C cleanroom hardware BOMs and knowledge digests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from logicaide.heritage.bom_extractor import extract_hardware_boms  # noqa: E402
from logicaide.heritage.doc_curator import curate_wave_c_knowledge  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    """Build argument parser for cleanroom heritage BOM and digest curation."""
    parser = argparse.ArgumentParser(
        description="Curate Wave C cleanroom hardware BOMs and knowledge digests from read-only private_references/.",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path("private_references"),
        help="Read-only private references root directory (default: private_references).",
    )
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=Path("knowledge"),
        help="Target cleanroom knowledge directory (default: knowledge).",
    )
    parser.add_argument(
        "--presets-dir",
        type=Path,
        default=Path("profiles/presets"),
        help="Target directory for hardware_boms.json (default: profiles/presets).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--bom-only",
        action="store_true",
        help="Run only cleanroom hardware BOM extraction.",
    )
    mode.add_argument(
        "--docs-only",
        action="store_true",
        help="Run only cleanroom knowledge digest curation and validation.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit curation summary as deterministic JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run cleanroom BOM extraction and/or knowledge curation."""
    args = _build_parser().parse_args(argv)
    summary: dict[str, Any] = {
        "private_root": str(args.private_root),
        "knowledge_dir": str(args.knowledge_dir),
        "presets_dir": str(args.presets_dir),
    }

    if not args.docs_only:
        output_json = args.presets_dir / "hardware_boms.json"
        output_md = args.knowledge_dir / "tier2_verified" / "hardware_adapter_boms.md"
        bom_payload = extract_hardware_boms(
            private_root=args.private_root,
            output_json=output_json,
            output_md=output_md,
        )
        summary["hardware_boms"] = {
            "output_json": str(output_json),
            "output_md": str(output_md),
            "preset_count": bom_payload["preset_count"],
            "preset_ids": [p["preset_id"] for p in bom_payload["presets"]],
        }

    if not args.bom_only:
        summary["knowledge"] = curate_wave_c_knowledge(
            private_root=args.private_root,
            knowledge_dir=args.knowledge_dir,
        )

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if "hardware_boms" in summary:
        hw = summary["hardware_boms"]
        print(
            f"Extracted {hw['preset_count']} hardware BOM presets "
            f"-> {hw['output_json']} & {hw['output_md']}"
        )
    if "knowledge" in summary:
        kn = summary["knowledge"]
        print(f"Curated {kn['curated_count']} knowledge/ digests -> {args.knowledge_dir}")
        for digest in kn["digests"]:
            print(f"  - {digest['path']} ({digest['trust_tier']}, {digest['lines']} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
