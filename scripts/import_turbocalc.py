#!/usr/bin/env python3
"""CLI wrapper to import TurboCalc v5 compressor maps into LogicAIDE profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from logicaide.profiles.turbo_importer import run_import_turbocalc  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import TurboCalc v5 archives/SQL/XML/DOCX into profiles/turbo/.",
    )
    parser.add_argument("--repo-root", type=Path, default=_REPO_ROOT, help="Repository root path")
    parser.add_argument("--rar-path", type=Path, default=None, help="Override path to turbocalc.rar")
    parser.add_argument("--sql-path", type=Path, default=None, help="Direct tools.sql path (skips unar)")
    parser.add_argument("--maps-dir", type=Path, default=None, help="Override beta 4 maps/ directory")
    parser.add_argument("--docx-path", type=Path, default=None, help="Override turbodata DOCX path")
    parser.add_argument("--xml-path", type=Path, default=None, help="Override turbocalc.xml path")
    parser.add_argument("--profiles-dir", type=Path, default=None, help="Output profiles/turbo/ directory")
    parser.add_argument("--figures-dir", type=Path, default=None, help="Output deduplicated figures directory")
    parser.add_argument("--examples-dir", type=Path, default=None, help="Override profiles/examples/ directory")
    parser.add_argument("--json", action="store_true", help="Emit import summary as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the TurboCalc importer."""
    args = _build_parser().parse_args(argv)
    summary = run_import_turbocalc(
        repo_root=args.repo_root,
        rar_path=args.rar_path,
        sql_path=args.sql_path,
        maps_dir=args.maps_dir,
        docx_path=args.docx_path,
        xml_path=args.xml_path,
        profiles_dir=args.profiles_dir,
        figures_dir=args.figures_dir,
        examples_dir=args.examples_dir,
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"Imported {summary['calibrated_count']} calibrated turbos "
            f"({summary['company_count']} companies, {summary['family_count']} families), "
            f"{summary['stub_count']} uncalibrated stubs, "
            f"{summary['unique_figure_count']}/{summary['source_figure_count']} unique figures, "
            f"{summary['alias_count']} example aliases."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
