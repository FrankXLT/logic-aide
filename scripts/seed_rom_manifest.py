#!/usr/bin/env python3
"""CLI wrapper to seed partitioned ROM manifests and optional CTAGS from private_references."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from logicaide.rom.manifest import build_partitioned_manifests, sync_manifests  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    """Build argument parser for ROM manifest seeding."""
    parser = argparse.ArgumentParser(
        description="Build partitioned ROM manifests from read-only private_references/ and optionally sync ctags.",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path("private_references"),
        help="Read-only private references root directory (default: private_references).",
    )
    parser.add_argument(
        "--rom-dir",
        type=Path,
        default=Path("rom"),
        help="Target ROM workspace directory for manifests and crossrefs (default: rom).",
    )
    parser.add_argument(
        "--baselines-dir",
        type=Path,
        default=None,
        help="Optional staged stock baselines directory (defaults to <rom-dir>/stock_baselines).",
    )
    parser.add_argument(
        "--sync-tags",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Generate gitignored Exuberant CTAGS file after building manifests.",
    )
    parser.add_argument(
        "--tags-path",
        type=Path,
        default=Path("tags"),
        help="Destination CTAGS file path when --sync-tags is enabled (default: tags).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit manifest seeding summary as JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, build partitioned ROM manifests, and optionally sync CTAGS."""
    args = _build_parser().parse_args(argv)
    summary = build_partitioned_manifests(
        args.private_root,
        rom_dir=args.rom_dir,
        baselines_dir=args.baselines_dir,
    )
    if args.sync_tags:
        summary["tags"] = sync_manifests(
            catalog_path=Path(summary["catalog_path"]),
            tags_path=args.tags_path,
            rom_dir=args.rom_dir,
        )

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"Seeded {summary['manifest_count']} ROM manifests -> {summary['catalog_path']}")
    for rom_id, stats in summary["manifests"].items():
        print(
            f"  - {rom_id}: verified_ram={stats['verified_ram_count']}, "
            f"cal_tables={stats['calibration_table_count']}, "
            f"baselines_present={stats['baselines_present_count']}/{stats['baseline_count']}"
        )
    if args.sync_tags and "tags" in summary:
        tags_info = summary["tags"]
        print(f"Synced {tags_info['tag_count']} ctags -> {tags_info['tags_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
