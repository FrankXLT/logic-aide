"""LogicAIDE command-line interface."""

from __future__ import annotations

import json
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

    try:
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
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


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


@main.group(help="Vehicle profile compiler and SQLite catalog commands.")
def profile() -> None:
    """Vehicle profile compiler and SQLite catalog commands."""


@profile.command(name="compile")
@click.argument("profile_path", type=click.Path(path_type=Path))
@click.option("--repo-root", type=click.Path(path_type=Path), default=None, help="Repository root containing schema, catalogs, and ROM manifests.")
@click.option("--db-path", type=click.Path(path_type=Path), default=None, help="Override SQLite catalog database path.")
@click.option("--persist/--no-persist", default=True, show_default=True, help="Persist compiled vehicle and revision to the SQLite catalog.")
@click.option("--require-rom-manifest/--no-require-rom-manifest", default=True, show_default=True, help="Fail if rom_manifest_ref cannot be resolved.")
@click.option("--json", "as_json", is_flag=True, help="Emit compiled report as JSON.")
def profile_compile(
    profile_path: Path,
    repo_root: Path | None,
    db_path: Path | None,
    persist: bool,
    require_rom_manifest: bool,
    as_json: bool,
) -> None:
    """Validate, gate-check, and optionally persist a vehicle build profile."""
    from logicaide.profiles.compiler import ProfileCompilationError, compile_profile

    try:
        report = compile_profile(
            profile_path,
            repo_root=repo_root,
            db_path=db_path,
            persist=persist,
            require_rom_manifest=require_rom_manifest,
        )
    except ProfileCompilationError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    rom_id = report["rom_manifest"].get("rom_id") or "unresolved"
    revision_id = (report.get("catalog_record") or {}).get("revision_id") or "not-persisted"
    eff_flow = report["fuel_scaling"]["declared_effective_flow_lb_hr"]
    click.echo(
        f"Compiled {report['car_id']} | tier={report['compatibility_tier']} "
        f"| swap_checklist={len(report['swap_checklist'])} "
        f"| eff_flow={eff_flow:.2f} lb/hr "
        f"| turbo={report['turbo']['canonical_turbo_id']} "
        f"| rom_id={rom_id} | revision_id={revision_id}"
    )


telemetry = _register_stub_group(
    "telemetry",
    "M2",
    "62.5k SCI telemetry poller and DuckDB lake commands",
    ("watch", "export-fixture"),
)


@main.group(help="68HC11/6803 ROM manifests, behavioral twins, and safety gate commands.")
def sim() -> None:
    """68HC11/6803 ROM manifests, behavioral twins, and safety gate commands."""


@sim.command(name="manifest-sync")
@click.option("--rom-id", default=None, help="Restrict CTAGS generation to one ROM ID.")
@click.option("--catalog", "catalog_path", type=click.Path(path_type=Path), default=Path("rom/manifests/catalog.json"), show_default=True)
@click.option("--tags-path", type=click.Path(path_type=Path), default=Path("tags"), show_default=True)
@click.option("--rom-dir", type=click.Path(path_type=Path), default=Path("rom"), show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit sync summary as JSON.")
def sim_manifest_sync(
    rom_id: str | None,
    catalog_path: Path,
    tags_path: Path,
    rom_dir: Path,
    as_json: bool,
) -> None:
    """Synchronize Exuberant CTAGS from partitioned ROM manifests and twin ASTs."""
    from logicaide.rom.manifest import sync_manifests

    try:
        summary = sync_manifests(
            catalog_path=catalog_path,
            rom_id=rom_id,
            tags_path=tags_path,
            rom_dir=rom_dir,
        )
    except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(summary, indent=2, sort_keys=True))
        return

    rom_list = ", ".join(summary["rom_ids"]) or "none"
    click.echo(f"Synced {summary['tag_count']} ctags ({rom_list}) -> {summary['tags_path']}")


@sim.command(name="lookup-sci")
@click.option("--rom-id", required=True, help="Target ROM manifest ID (e.g., smec_88_89_v19, sbec_90_93_v3_1, lm_86_87_v13, lm_86_glhs_v1).")
@click.option("--sci-table", default=None, help="Optional SCI table selector (0xF2, 0xF6, LM_DIRECT_RAM, LM_IO).")
@click.option("--offset", default=None, help="Hex (0x63/$63) or decimal (99) RAM/I/O offset.")
@click.option("--var", "var_name", default=None, help="Symbol, short name, or long name.")
@click.option("--catalog", "catalog_path", type=click.Path(path_type=Path), default=Path("rom/manifests/catalog.json"), show_default=True)
@click.option("--strict/--no-strict", default=True, show_default=True, help="Require source-verified watchlist corroboration.")
@click.option("--json", "as_json", is_flag=True, help="Emit lookup result as JSON.")
def sim_lookup_sci(
    rom_id: str,
    sci_table: str | None,
    offset: str | None,
    var_name: str | None,
    catalog_path: Path,
    strict: bool,
    as_json: bool,
) -> None:
    """Look up a source-verified SCI offset or variable in a partitioned ROM manifest."""
    if var_name is not None and not var_name.strip():
        raise click.UsageError("--var must not be empty.")
    if offset is None and var_name is None:
        raise click.UsageError("Specify at least one of --offset or --var.")

    from logicaide.rom.manifest import lookup_sci

    try:
        result = lookup_sci(
            rom_id,
            offset=offset,
            var_name=var_name,
            sci_table=sci_table,
            strict=strict,
            catalog_path=catalog_path,
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    if not result["matched"]:
        click.echo(result.get("message", "No Verified Source Match"))
        return

    entry = result.get("entry") or {}
    settings = entry.get("settings") or {}
    units = settings.get("units") or "n/a"
    d_min, d_max = settings.get("display_min"), settings.get("display_max")
    e_min, e_max = settings.get("ecu_min"), settings.get("ecu_max")
    range_str = (
        f"{d_min}..{d_max}" if d_min is not None and d_max is not None
        else (f"{e_min}..{e_max}" if e_min is not None and e_max is not None else "n/a")
    )
    source_verified = bool(entry.get("source_verified", result.get("status") == "VERIFIED_SOURCE_MATCH"))
    click.echo(
        f"{result.get('var_name')} | offset={result.get('offset')} "
        f"| table={result.get('sci_table')} | units={units} "
        f"| range={range_str} | source_verified={source_verified}"
    )


if __name__ == "__main__":
    main()
