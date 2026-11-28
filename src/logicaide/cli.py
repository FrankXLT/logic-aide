"""LogicAIDE command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

import click
import duckdb

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
                manifest_path=manifest,
                queue_dir=queue_dir,
                output_yaml=output_yaml,
            ),
        )
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@heritage.command(name="sync-wiki")
@click.option("--wiki-dir", type=click.Path(path_type=Path), default=Path("wiki"), show_default=True, help="Source wiki directory to synchronize.")
@click.option("--target-dir", type=click.Path(path_type=Path), default=None, help="Target directory for synchronized wiki outputs.")
@click.option("--dry-run", is_flag=True, default=False, help="Preview wiki synchronization changes without writing files.")
@click.option("--validate-only", is_flag=True, default=False, help="Validate wiki pages without copying or modifying targets.")
@click.option("--strip-frontmatter/--no-strip-frontmatter", default=True, show_default=True, help="Strip YAML frontmatter on outbound .wiki.git copies.")
@click.option("--flatten/--no-flatten", default=True, show_default=True, help="Flatten subdirectories for GitHub .wiki.git or preserve tree for static sites.")
@click.option("--push-github-wiki", is_flag=True, default=False, help="Validate, flatten, commit, and mirror wiki pages to GitHub .wiki.git.")
@click.option("--wiki-remote", type=str, default=None, help="Override GitHub Wiki git remote URL (defaults to origin .wiki.git).")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit wiki sync summary as JSON.")
def sync_wiki(
    wiki_dir: Path,
    target_dir: Path | None,
    dry_run: bool,
    validate_only: bool,
    strip_frontmatter: bool,
    flatten: bool,
    push_github_wiki: bool,
    wiki_remote: str | None,
    as_json: bool,
) -> None:
    """Validate and synchronize heritage wiki pages."""
    from logicaide.heritage.wiki_sync import WikiValidationError, run_sync_wiki

    if validate_only and (push_github_wiki or wiki_remote is not None):
        raise click.UsageError("--validate-only cannot be combined with --push-github-wiki or --wiki-remote.")
    if wiki_remote is not None and not push_github_wiki:
        raise click.UsageError("--wiki-remote requires --push-github-wiki.")
    try:
        result = run_sync_wiki(
            wiki_dir=wiki_dir,
            dry_run=dry_run,
            validate_only=validate_only,
            strip_frontmatter=strip_frontmatter,
            flatten_for_github_wiki=flatten,
            push_github_wiki=push_github_wiki,
            wiki_remote=wiki_remote,
            **_compact_kwargs(target_dir=target_dir),
        )
    except (WikiValidationError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    emit_sync_wiki_result(result, as_json)


from logicaide.heritage.cli_commands import emit_sync_wiki_result, register_heritage_commands

register_heritage_commands(heritage)


_CAR_ID_BY_FORMAT: dict[str, str] = {
    "lasky_realtimelog": "lm_87_realtimelog",
    "wot_log_mmss": "smec_89_mpscandroid",
    "k_smec_13col": "smec_k_logs36",
    "ecu_dlg": "sbec_90_van_dlg",
}


def _resolve_car_id(car_id: str, source_format: str) -> str:
    """Resolve ``--car-id auto`` from parsed telemetry source format."""
    if car_id != "auto":
        return car_id
    if source_format.startswith("mpscan_android_"):
        return "smec_89_mpscandroid"
    return _CAR_ID_BY_FORMAT.get(source_format, "CHRYSLER_UNKNOWN")


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


@main.group(help="62.5k SCI telemetry poller, Zstd Parquet lake, and DuckDB rollup commands.")
def telemetry() -> None:
    """62.5k SCI telemetry poller, Zstd Parquet lake, and DuckDB rollup commands."""


@telemetry.command(name="ingest")
@click.option("--input", "input_path", type=click.Path(path_type=Path), default=Path("private_references"), show_default=True, help="Telemetry log file or root directory to ingest.")
@click.option("--lake-dir", type=click.Path(path_type=Path), default=Path("data/drives"), show_default=True, help="Destination Hive-partitioned Zstd Parquet lake directory.")
@click.option("--db-path", type=click.Path(path_type=Path), default=Path("data/drives/catalog.sqlite"), show_default=True, help="SQLite catalog database path.")
@click.option("--car-id", default="auto", show_default=True, help="Vehicle car_id or 'auto' to map from source format.")
@click.option("--build-id", default="STOCK", show_default=True, help="Calibration build identifier.")
@click.option("--run-kind", default="VEHICLE_SCI", show_default=True, help="Telemetry run kind (VEHICLE_SCI, SINGLE_REPLAY, MONTE_CARLO_SWEEP).")
@click.option("--deduplicate/--no-deduplicate", default=True, show_default=True, help="Deduplicate identical log files by SHA-256 when scanning a directory.")
@click.option("--json", "as_json", is_flag=True, help="Emit ingestion summary as JSON.")
def telemetry_ingest(
    input_path: Path, lake_dir: Path, db_path: Path, car_id: str,
    build_id: str, run_kind: str, deduplicate: bool, as_json: bool,
) -> None:
    """Ingest empirical telemetry logs into 30s Zstd Parquet chunks and SQLite catalog."""
    from logicaide.telemetry import discover_private_logs, parse_telemetry_file, write_session_parquet_chunks

    if not input_path.exists():
        raise click.ClickException(f"Telemetry input path not found: {input_path}")

    is_dir = input_path.is_dir()
    files = discover_private_logs(input_path, deduplicate=deduplicate) if is_dir else [input_path]
    private_root = input_path if is_dir else None
    parse_car = "CHRYSLER_UNKNOWN" if car_id == "auto" else car_id
    totals: dict[str, Any] = {
        "input_path": str(input_path), "lake_dir": str(lake_dir), "db_path": str(db_path),
        "files_processed": len(files), "session_count": 0, "chunk_count": 0,
        "row_count": 0, "quarantined_rows": 0, "car_ids": [],
    }
    seen_cars: set[str] = set()
    try:
        for path in files:
            sessions = parse_telemetry_file(
                path, car_id=parse_car, build_id=build_id, run_kind=run_kind, private_root=private_root
            )
            for sess in sessions:
                sess.car_id = _resolve_car_id(car_id, sess.source_format)
                res = write_session_parquet_chunks(sess, lake_dir=lake_dir, db_path=db_path)
                totals["session_count"] += 1
                totals["chunk_count"] += res["chunk_count"]
                totals["row_count"] += res["row_count"]
                totals["quarantined_rows"] += sess.quarantined_sample_count
                seen_cars.add(sess.car_id)
    except (FileNotFoundError, ValueError, OSError, sqlite3.Error, duckdb.Error) as exc:
        raise click.ClickException(str(exc)) from exc

    totals["car_ids"] = sorted(seen_cars)
    if as_json:
        click.echo(json.dumps(totals, indent=2, sort_keys=True))
        return
    click.echo(
        f"Ingested {totals['files_processed']} files -> {totals['session_count']} sessions, "
        f"{totals['chunk_count']} chunks, {totals['row_count']} rows "
        f"(quarantined={totals['quarantined_rows']}) -> {lake_dir}"
    )


@telemetry.command(name="rollups")
@click.option("--lake-dir", type=click.Path(path_type=Path), default=Path("data/drives"), show_default=True, help="Parquet lake root directory.")
@click.option("--car-id", default=None, help="Filter rollups to one car_id.")
@click.option("--run-kind", default=None, help="Filter rollups by run_kind.")
@click.option("--session-id", default=None, help="Filter rollups by session_id.")
@click.option("--build-id", default=None, help="Filter rollups by build_id.")
@click.option("--limit", type=int, default=None, help="Maximum rollup bins to return.")
@click.option("--json", "as_json", is_flag=True, help="Emit lake summary and load-cell rollups as JSON.")
def telemetry_rollups(
    lake_dir: Path, car_id: str | None, run_kind: str | None,
    session_id: str | None, build_id: str | None, limit: int | None, as_json: bool,
) -> None:
    """Query DuckDB load_cell_rollups across the Zstd Parquet telemetry lake."""
    from logicaide.telemetry import query_load_cell_rollups, summarize_telemetry_lake

    try:
        summary = summarize_telemetry_lake(lake_dir)
        bins = query_load_cell_rollups(
            lake_dir, car_id=car_id, run_kind=run_kind, session_id=session_id, build_id=build_id, limit=limit
        )
    except (FileNotFoundError, ValueError, OSError, sqlite3.Error, duckdb.Error) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps({"summary": summary, "bin_count": len(bins), "rollups": bins}, indent=2, sort_keys=True))
        return

    click.echo(
        f"Lake {summary['lake_dir']} | sessions={summary['session_count']} | chunks={summary['chunk_count']} "
        f"| active_rows={summary['active_rows']} | quarantined={summary['quarantined_rows']} | bins={len(bins)}"
    )
    for row in bins[:10]:
        afr = f"{row['mean_afr']:.2f}" if row["mean_afr"] is not None else "null"
        pw = f"{row['mean_pw_ms']:.2f}" if row["mean_pw_ms"] is not None else "null"
        click.echo(
            f"  - {row['car_id']} {row['rpm_bin']}rpm {row['map_bin_kpa']}kPa "
            f"{row['tps_regime']} {row['charge_temp_bin']} n={row['sample_count']} afr={afr} pw_ms={pw}"
        )


@telemetry.command(name="export-fixture")
@click.option("--source", type=click.Path(path_type=Path), default=Path("data/drives"), show_default=True, help="Source Parquet lake directory or telemetry file.")
@click.option("--output", type=click.Path(path_type=Path), required=True, help="Destination GPS-stripped fixture file.")
@click.option("--max-rows", type=int, default=150, show_default=True, help="Maximum rows to export.")
@click.option("--format", "fmt", type=click.Choice(["csv", "json", "parquet"], case_sensitive=False), default="csv", show_default=True, help="Output fixture format.")
@click.option("--car-id", default=None, help="Optional car_id filter.")
@click.option("--session-id", default=None, help="Optional session_id filter.")
@click.option("--db-path", type=click.Path(path_type=Path), default=None, help="Optional SQLite catalog path to record stimulus_fixtures.")
@click.option("--issue-id", default=None, help="Optional tuning_issues identifier linked to this fixture.")
@click.option("--json", "as_json", is_flag=True, help="Emit export metadata as JSON.")
def telemetry_export_fixture(
    source: Path, output: Path, max_rows: int, fmt: str, car_id: str | None,
    session_id: str | None, db_path: Path | None, issue_id: str | None, as_json: bool,
) -> None:
    """Export a GPS-stripped, PII-scrubbed stimulus fixture slice."""
    from logicaide.telemetry import export_stimulus_fixture

    try:
        result = export_stimulus_fixture(
            source, output, max_rows=max_rows, fmt=fmt, car_id=car_id, session_id=session_id,
            db_path=db_path, issue_id=issue_id,
        )
    except (FileNotFoundError, ValueError, OSError, sqlite3.Error, duckdb.Error) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    click.echo(
        f"Exported {result['row_count']} rows ({result['format']}) -> {result['output_path']} "
        f"| gps_stripped={result['gps_stripped']} | sha256={result['sha256'][:12]}..."
    )


from logicaide.telemetry.watch_cli import register_telemetry_watch_command

register_telemetry_watch_command(telemetry)


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


from logicaide.sim.cli_sim import register_sim_commands
from logicaide.workbench.cli_commands import register_workbench_commands

register_sim_commands(sim)
register_workbench_commands(main, telemetry)


if __name__ == "__main__":
    main()
