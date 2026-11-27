#!/usr/bin/env python3
"""CLI wrapper to extract Wave D PDFs and validate cleanroom knowledge digests."""

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

from logicaide.heritage.doc_curator import validate_cleanroom_digest  # noqa: E402
from logicaide.heritage.pdf_extractor import run_parse_docs  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    """Build argument parser for Wave D PDF extraction and cleanroom digest validation."""
    parser = argparse.ArgumentParser(
        description="Extract Wave D PDFs into gitignored data/heritage_raw/ and validate cleanroom digests.",
    )
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path("private_references"),
        help="Read-only private references root or single PDF path (default: private_references).",
    )
    parser.add_argument(
        "--output-md-dir",
        type=Path,
        default=Path("data/heritage_raw/pdfs_md"),
        help="Gitignored output directory for extracted Markdown and metadata (default: data/heritage_raw/pdfs_md).",
    )
    parser.add_argument(
        "--figures-dir",
        type=Path,
        default=Path("data/heritage_raw/figures"),
        help="Gitignored output directory for rendered PDF page PNGs (default: data/heritage_raw/figures).",
    )
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=Path("knowledge"),
        help="Target cleanroom knowledge directory (default: knowledge).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--parse-only",
        action="store_true",
        help="Run only Wave D PDF parsing and OCR fallback rendering.",
    )
    mode.add_argument(
        "--validate-only",
        action="store_true",
        help="Run only cleanroom knowledge digest validation under tier1_oem/ and tier2_verified/.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional cap on pages extracted/rendered per PDF.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Render DPI for scanned/OCR-corrupted PDF fallback PNGs (default: 300).",
    )
    parser.add_argument(
        "--no-figures",
        action="store_true",
        help="Disable rendering fallback PNG pages for scanned/OCR-corrupted PDFs.",
    )
    parser.add_argument(
        "--verify-mermaid",
        action="store_true",
        help="Verify syntax headers of fenced Mermaid blocks in extracted Markdown.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on number of deduplicated PDFs to process.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit curation summary as deterministic JSON.",
    )
    return parser


def _validate_wave_d_digests(knowledge_dir: Path) -> dict[str, Any]:
    """Validate cleanroom digests under knowledge/tier1_oem/ and knowledge/tier2_verified/ when present."""
    validated: list[dict[str, Any]] = []
    for tier_subdir in ("tier1_oem", "tier2_verified"):
        tier_dir = knowledge_dir / tier_subdir
        if not tier_dir.is_dir():
            continue
        for md_path in sorted(tier_dir.glob("*.md")):
            fm = validate_cleanroom_digest(md_path)
            rel_path = md_path.relative_to(knowledge_dir).as_posix()
            line_count = len(md_path.read_text(encoding="utf-8").splitlines())
            validated.append(
                {
                    "path": rel_path,
                    "doc_id": fm["doc_id"],
                    "trust_tier": fm["trust_tier"],
                    "lines": line_count,
                }
            )
    return {"validated_count": len(validated), "digests": validated}


def _print_human_summary(summary: dict[str, Any], knowledge_dir: Path) -> None:
    """Print concise human-readable Wave D extraction and validation summary."""
    if "pdf_extraction" in summary:
        pdf = summary["pdf_extraction"]
        print(
            f"Processed {pdf['processed_count']} PDFs "
            f"(excluded={pdf['excluded_count']}, duplicates={pdf['duplicate_count']}) "
            f"-> {summary['output_md_dir']}"
        )
        for doc in pdf.get("documents", []):
            print(
                f"  - {doc['slug']} [{doc['track']}] "
                f"({doc['ocr_status']}, {doc['pages_processed']}/{doc['page_count']}p)"
            )
    if "knowledge_validation" in summary:
        kv = summary["knowledge_validation"]
        print(f"Validated {kv['validated_count']} cleanroom digests under {knowledge_dir}")
        for digest in kv["digests"]:
            print(f"  - {digest['path']} ({digest['trust_tier']}, {digest['lines']} lines)")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run Wave D PDF extraction and/or cleanroom digest validation."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    for flag, val in (("--max-pages", args.max_pages), ("--dpi", args.dpi), ("--limit", args.limit)):
        if val is not None and val < 1:
            parser.error(f"{flag} must be >= 1 (got {val})")
    summary: dict[str, Any] = {
        "private_root": str(args.private_root),
        "output_md_dir": str(args.output_md_dir),
        "figures_dir": str(args.figures_dir),
        "knowledge_dir": str(args.knowledge_dir),
    }

    if not args.validate_only:
        summary["pdf_extraction"] = run_parse_docs(
            args.private_root,
            output_md_dir=args.output_md_dir,
            figures_dir=args.figures_dir,
            render_dpi=args.dpi,
            max_pages=args.max_pages,
            render_figures=not args.no_figures,
            verify_mermaid=args.verify_mermaid,
            limit=args.limit,
        )

    if not args.parse_only:
        summary["knowledge_validation"] = _validate_wave_d_digests(args.knowledge_dir)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    _print_human_summary(summary, args.knowledge_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
