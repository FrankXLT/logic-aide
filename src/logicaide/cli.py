"""LogicAIDE command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from logicaide import __version__

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def _compact_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Return only keyword arguments whose values are not ``None``."""
    return {key: value for key, value in kwargs.items() if value is not None}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version=__version__, prog_name="logicaide")
def main() -> None:
    """LogicAIDE CLI for Chrysler 68HC11 calibration, telemetry, sim, and heritage."""


@main.group()
def turbo() -> None:
    """TurboCalc workbook import and injector/turbo preset extraction."""


@turbo.command(name="import-turbocalc")
@click.option("--source-dir", type=click.Path(path_type=Path), default=None, help="Directory containing TurboCalc data (turbocalc.rar, XML, DOCX, and maps).")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None, help="Destination directory for normalized turbo_spec.json profiles.")
@click.option("--figures-dir", type=click.Path(path_type=Path), default=None, help="Destination directory for extracted chart/figure assets.")
@click.option("--sql-path", type=click.Path(path_type=Path), default=None, help="Input tools.sql dump path (bypasses turbocalc.rar extraction).")
@click.option("--presets-dir", type=click.Path(path_type=Path), default=None, help="Destination directory for extracted injector and flowbench presets.")
@click.option("--private-root", type=click.Path(path_type=Path), default=None, help="Root directory containing private reference workbooks for presets.")
@click.option(
    "--include-presets/--no-presets",
    default=True,
    show_default=True,
    help="Also run injector preset extraction after TurboCalc import.",
)
def import_turbocalc(
    source_dir: Path | None,
    output_dir: Path | None,
    figures_dir: Path | None,
    sql_path: Path | None,
    presets_dir: Path | None,
    private_root: Path | None,
    include_presets: bool,
) -> None:
    """Import TurboCalc calibration data and optionally extract injector presets."""
    from logicaide.profiles.turbo_importer import run_import_turbocalc

    run_import_turbocalc(
        **_compact_kwargs(
            source_dir=source_dir,
            output_dir=output_dir,
            figures_dir=figures_dir,
            sql_path=sql_path,
        )
    )
    if include_presets:
        from logicaide.profiles.injector_presets import run_extract_presets

        run_extract_presets(
            **_compact_kwargs(
                private_root=private_root,
                output_presets_dir=presets_dir,
            )
        )


@turbo.command(name="extract-presets")
@click.option("--private-root", type=click.Path(path_type=Path), default=None, help="Root directory containing private reference workbooks.")
@click.option("--output-dir", type=click.Path(path_type=Path), default=None, help="Destination directory for extracted injector presets.")
def extract_presets(private_root: Path | None, output_dir: Path | None) -> None:
    """Extract injector and turbocharger presets from private reference sources."""
    from logicaide.profiles.injector_presets import run_extract_presets

    run_extract_presets(
        **_compact_kwargs(private_root=private_root, output_dir=output_dir)
    )


@main.group()
def heritage() -> None:
    """Chrysler heritage discovery, wiki synchronization, and RAG workflows."""


@heritage.command(name="discover")
@click.option("--wave", default="A", show_default=True, help="Discovery wave identifier to process.")
@click.option("--private-root", type=click.Path(path_type=Path), default=None, help="Root directory containing private heritage archives.")
@click.option("--manifest", type=click.Path(path_type=Path), default=None, help="Path to heritage discovery manifest.")
@click.option("--queue-dir", type=click.Path(path_type=Path), default=None, help="Directory for staged crawl queue files.")
@click.option("--output-yaml", type=click.Path(path_type=Path), default=None, help="Output YAML path for discovered source entries.")
@click.option(
    "--fetch-cdx/--no-fetch-cdx",
    default=False,
    show_default=True,
    help="Query Wayback CDX index for archived heritage URLs.",
)
def discover(
    wave: str,
    private_root: Path | None,
    manifest: Path | None,
    queue_dir: Path | None,
    output_yaml: Path | None,
    fetch_cdx: bool,
) -> None:
    """Discover heritage documents and build wave crawl queues."""
    from logicaide.heritage.discovery import run_discover

    run_discover(
        wave=wave,
        fetch_cdx=fetch_cdx,
        **_compact_kwargs(
            private_root=private_root,
            manifest=manifest,
            queue_dir=queue_dir,
            output_yaml=output_yaml,
        ),
    )


@heritage.command(name="sync-wiki")
@click.option(
    "--wiki-dir",
    type=click.Path(path_type=Path),
    default=Path("wiki"),
    show_default=True,
    help="Source wiki directory to synchronize.",
)
@click.option("--target-dir", type=click.Path(path_type=Path), default=None, help="Target directory for synchronized wiki outputs.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview wiki synchronization changes without writing files.")
@click.option("--validate-only", is_flag=True, default=False, help="Validate wiki pages without copying or modifying targets.")
@click.option("--strip-frontmatter/--no-strip-frontmatter", default=True, show_default=True, help="Strip YAML frontmatter on outbound .wiki.git copies.")
@click.option("--flatten/--no-flatten", default=True, show_default=True, help="Flatten subdirectories for GitHub .wiki.git or preserve tree for static sites.")
def sync_wiki(
    wiki_dir: Path,
    target_dir: Path | None,
    dry_run: bool,
    validate_only: bool,
    strip_frontmatter: bool,
    flatten: bool,
) -> None:
    """Validate and synchronize heritage wiki pages."""
    from logicaide.heritage.wiki_sync import run_sync_wiki

    run_sync_wiki(
        wiki_dir=wiki_dir,
        dry_run=dry_run,
        validate_only=validate_only,
        strip_frontmatter=strip_frontmatter,
        flatten_for_github_wiki=flatten,
        **_compact_kwargs(target_dir=target_dir),
    )


def _register_stub_group(
    name: str,
    milestone: str,
    summary: str,
    planned: tuple[str, ...],
) -> click.Group:
    """Register a placeholder milestone subgroup on ``main``."""
    planned_list = ", ".join(planned)
    help_text = f"{summary} (Milestone {milestone}).\n\nPlanned subcommands: {planned_list}."

    @main.group(
        name=name,
        invoke_without_command=True,
        help=help_text,
        short_help=f"{summary} ({milestone}).",
    )
    @click.pass_context
    def _stub_group(ctx: click.Context) -> None:
        if ctx.invoked_subcommand is None:
            click.echo(
                f"`logicaide {name}` is scheduled for Milestone {milestone}. "
                f"Planned subcommands: {planned_list}."
            )

    return _stub_group


profile = _register_stub_group(
    "profile",
    "M1",
    "Vehicle profile compiler and SQLite catalog commands",
    ("compile",),
)
telemetry = _register_stub_group(
    "telemetry",
    "M2",
    "62.5k SCI telemetry poller and DuckDB lake commands",
    ("watch", "export-fixture"),
)
sim = _register_stub_group(
    "sim",
    "M3",
    "68HC11 emulator, behavioral twins, and safety gate commands",
    ("manifest-sync",),
)


if __name__ == "__main__":
    main()
