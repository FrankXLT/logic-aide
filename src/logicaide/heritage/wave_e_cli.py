"""Wave E CLI subcommand registrations (`crawl`, `parse-web`) for ``logicaide heritage``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from logicaide.heritage.practitioner_graph import DEFAULT_ROSTER_PATH
from logicaide.heritage.wave_e_pipeline import (
    DEFAULT_FIGURES_ROOT,
    DEFAULT_QUEUE_PATH,
    DEFAULT_WARC_ROOT,
    DEFAULT_WEB_MD_DIR,
    WAVE_E_SOURCES,
    run_wave_e_crawl,
    run_wave_e_parse_web,
)

_HANDLED_ERRORS = (RuntimeError, FileNotFoundError, KeyError, ValueError, TypeError, OSError)


def _emit_json(payload: dict[str, Any]) -> None:
    """Print a dictionary as deterministic sorted JSON."""
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


@click.command(name="crawl")
@click.option("--source", "sources", multiple=True, type=click.Choice(WAVE_E_SOURCES, case_sensitive=False), help="Wave E source(s) to crawl (repeatable; defaults to all 4 canonical sources).")
@click.option("--max-pages", type=click.IntRange(min=1), default=35, show_default=True, help="Maximum pages to attempt per source.")
@click.option("--queue-path", type=click.Path(path_type=Path), default=DEFAULT_QUEUE_PATH, show_default=True, help="Path to wave_e_queue.json discovery queue.")
@click.option("--warc-root", type=click.Path(path_type=Path), default=DEFAULT_WARC_ROOT, show_default=True, help="Destination root directory for ISO 28500 WARC archives.")
@click.option("--figures-root", type=click.Path(path_type=Path), default=DEFAULT_FIGURES_ROOT, show_default=True, help="Destination root directory for deduplicated inline figure images.")
@click.option("--no-images", is_flag=True, default=False, help="Skip downloading inline figure images during HTML capture.")
@click.option("--fetch-cdx", is_flag=True, default=False, help="Query Wayback CDX when queue items lack pre-fetched snapshots.")
@click.option("--delay", type=click.FloatRange(min=0.0), default=1.2, show_default=True, help="Polite per-host request delay in seconds.")
@click.option("--json", "as_json", is_flag=True, help="Emit crawl summary as JSON.")
def crawl_cmd(
    sources: tuple[str, ...], max_pages: int, queue_path: Path, warc_root: Path,
    figures_root: Path, no_images: bool, fetch_cdx: bool, delay: float, as_json: bool,
) -> None:
    """Crawl bounded Wave E heritage sources into ISO 28500 WARC vaults."""
    try:
        result = run_wave_e_crawl(
            sources=sources or None,
            max_pages_per_source=max_pages,
            queue_path=queue_path,
            warc_root=warc_root,
            figures_root=figures_root,
            fetch_images=not no_images,
            fetch_cdx=fetch_cdx,
            delay_s=delay,
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit_json(result)
        return
    click.echo(
        f"Wave E crawl complete: sources={','.join(result['sources'])} "
        f"captured={result['captured_count']} deduplicated={result['deduplicated_count']} "
        f"blocked={result['blocked_count']} errors={result['error_count']} -> {result['warc_root']}"
    )


@click.command(name="parse-web")
@click.option(
    "--warc-root",
    type=click.Path(path_type=Path),
    default=DEFAULT_WARC_ROOT,
    show_default=True,
    help="Root directory containing .warc / .warc.gz archives.",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_WEB_MD_DIR,
    show_default=True,
    help="Output directory for cleanroom normalized Markdown and parse_manifest.json.",
)
@click.option(
    "--roster-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_ROSTER_PATH,
    show_default=True,
    help="YAML path for the 3-pass practitioner authority roster.",
)
@click.option(
    "--local-archive-dir",
    "local_archive_dirs",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Optional local HTML archive directory to normalize alongside WARCs (repeatable).",
)
@click.option(
    "--pin",
    "pin_handles",
    multiple=True,
    type=str,
    help="Pin a forum handle as CORE_ECU_PRACTITIONER in the roster (repeatable).",
)
@click.option(
    "--mute",
    "mute_handles",
    multiple=True,
    type=str,
    help="Mute a forum handle and its endorsements in the roster (repeatable).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit parse-web summary as JSON.")
def parse_web_cmd(
    warc_root: Path,
    output_dir: Path,
    roster_path: Path,
    local_archive_dirs: tuple[Path, ...],
    pin_handles: tuple[str, ...],
    mute_handles: tuple[str, ...],
    as_json: bool,
) -> None:
    """Normalize WARC + safe local HTML archives into cleanroom Markdown and forum threads."""
    try:
        result = run_wave_e_parse_web(
            warc_root=warc_root,
            output_dir=output_dir,
            roster_path=roster_path,
            local_archive_dirs=local_archive_dirs,
            pin_handles=pin_handles,
            mute_handles=mute_handles,
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit_json(result)
        return
    click.echo(
        f"Normalized {result['static_docs_written']} static docs and "
        f"{result['forum_threads_written']} forum threads "
        f"(Tier 2={result['tier2_threads']}, Tier 3={result['tier3_threads']}, "
        f"errors={len(result['errors'])}) -> {output_dir / 'parse_manifest.json'}"
    )


def register_wave_e_commands(heritage_group: click.Group) -> None:
    """Register Wave E ``crawl`` and ``parse-web`` subcommands onto ``heritage_group``."""
    heritage_group.add_command(crawl_cmd)
    heritage_group.add_command(parse_web_cmd)
