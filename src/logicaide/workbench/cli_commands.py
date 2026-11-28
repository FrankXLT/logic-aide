"""CLI command registration for Agent Briefs, workbench readiness, and tuning issues."""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import sqlite3
from typing import Any

import click
import duckdb

from logicaide.catalog.sqlite_store import DEFAULT_CATALOG_DB
from logicaide.heritage.rag_index import DEFAULT_INDEX_DB
from logicaide.rom.manifest import DEFAULT_CATALOG_PATH
from logicaide.telemetry.issues import (
    create_or_update_tuning_issue, get_tuning_issue, list_tuning_issues,
)
from logicaide.telemetry.watch_cli import DEFAULT_LAKE_DIR
from logicaide.workbench.briefs import (
    DEFAULT_STAGING_ROOT, build_candidate_build_brief, build_issue_brief,
    build_load_cell_brief, build_session_brief,
)
from logicaide.workbench.readiness import check_workbench_readiness, format_readiness_text


def _emit_brief(loader: Callable[[], dict[str, Any]], as_json: bool, output_format: str) -> None:
    try:
        brief = loader()
    except (KeyError, FileNotFoundError, ValueError, TypeError, OSError, sqlite3.Error, duckdb.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json or output_format.lower() == "json":
        click.echo(json.dumps(brief, indent=2, sort_keys=True))
        return
    click.echo(brief["markdown"], nl=False)


def _emit_issue_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    click.echo(
        f"{str(result.get('action', 'saved')).capitalize()} {result['issue_id']} "
        f"| status={result['status']} | severity={result['severity']} "
        f"| car={result['car_id']} | title={result['title']}"
    )


def _register_brief_commands(main_group: click.Group) -> None:
    @main_group.group(name="brief", help="Generate GPS/PII-free Agent Briefs for sessions, cells, issues, and builds.")
    def brief() -> None:
        """Generate GPS/PII-free Agent Briefs for LogicAIDE workbench contexts."""

    @brief.command(name="session")
    @click.argument("session_id")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--lake-dir", type=click.Path(path_type=Path), default=DEFAULT_LAKE_DIR, show_default=True)
    @click.option("--format", "output_format", type=click.Choice(["markdown", "json"], case_sensitive=False), default="markdown", show_default=True)
    @click.option("--json", "as_json", is_flag=True, help="Emit brief as JSON.")
    def brief_session(session_id: str, db_path: Path, lake_dir: Path, output_format: str, as_json: bool) -> None:
        """Generate an Agent Brief for a telemetry drive or replay session."""
        _emit_brief(lambda: build_session_brief(session_id, db_path=db_path, lake_dir=lake_dir), as_json, output_format)

    @brief.command(name="cell")
    @click.option("--rpm-bin", type=int, required=True, help="Exact 250 RPM bucket start (e.g., 3500).")
    @click.option("--map-bin", "map_bin_kpa", type=int, required=True, help="Exact 10 kPa MAP bucket start (e.g., 160).")
    @click.option("--lake-dir", type=click.Path(path_type=Path), default=DEFAULT_LAKE_DIR, show_default=True)
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--car-id", default=None, help="Optional car_id filter.")
    @click.option("--session-id", default=None, help="Optional session_id filter.")
    @click.option("--build-id", default=None, help="Optional build_id filter.")
    @click.option("--run-kind", default=None, help="Optional run_kind filter.")
    @click.option("--tps-regime", default=None, help="Optional tps_regime filter.")
    @click.option("--charge-temp-bin", default=None, help="Optional charge_temp_bin filter.")
    @click.option("--format", "output_format", type=click.Choice(["markdown", "json"], case_sensitive=False), default="markdown", show_default=True)
    @click.option("--json", "as_json", is_flag=True, help="Emit brief as JSON.")
    def brief_cell(
        rpm_bin: int, map_bin_kpa: int, lake_dir: Path, db_path: Path, car_id: str | None,
        session_id: str | None, build_id: str | None, run_kind: str | None,
        tps_regime: str | None, charge_temp_bin: str | None, output_format: str, as_json: bool,
    ) -> None:
        """Generate an Agent Brief for an exact (rpm_bin, map_bin_kpa) load cell."""
        _emit_brief(
            lambda: build_load_cell_brief(
                rpm_bin, map_bin_kpa, lake_dir=lake_dir, db_path=db_path, car_id=car_id,
                session_id=session_id, build_id=build_id, run_kind=run_kind,
                tps_regime=tps_regime, charge_temp_bin=charge_temp_bin,
            ),
            as_json,
            output_format,
        )

    @brief.command(name="issue")
    @click.argument("issue_id")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--lake-dir", type=click.Path(path_type=Path), default=DEFAULT_LAKE_DIR, show_default=True)
    @click.option("--format", "output_format", type=click.Choice(["markdown", "json"], case_sensitive=False), default="markdown", show_default=True)
    @click.option("--json", "as_json", is_flag=True, help="Emit brief as JSON.")
    def brief_issue(issue_id: str, db_path: Path, lake_dir: Path, output_format: str, as_json: bool) -> None:
        """Generate an Agent Brief for a tuning issue with surrounding lake context."""
        _emit_brief(lambda: build_issue_brief(issue_id, db_path=db_path, lake_dir=lake_dir), as_json, output_format)

    @brief.command(name="build")
    @click.argument("build_id")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--staging-root", type=click.Path(path_type=Path), default=DEFAULT_STAGING_ROOT, show_default=True)
    @click.option("--format", "output_format", type=click.Choice(["markdown", "json"], case_sensitive=False), default="markdown", show_default=True)
    @click.option("--json", "as_json", is_flag=True, help="Emit brief as JSON.")
    def brief_build(build_id: str, db_path: Path, staging_root: Path, output_format: str, as_json: bool) -> None:
        """Generate a Hardware-Deadbolt-enforced Agent Brief for a staged candidate build."""
        _emit_brief(lambda: build_candidate_build_brief(build_id, db_path=db_path, staging_root=staging_root), as_json, output_format)


def _register_readiness_command(main_group: click.Group) -> None:
    @main_group.command(name="readiness", help="Check readiness across catalog, telemetry lake, ROM manifests, and heritage RAG.")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--lake-dir", type=click.Path(path_type=Path), default=DEFAULT_LAKE_DIR, show_default=True)
    @click.option("--rom-catalog", "rom_catalog_path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_PATH, show_default=True)
    @click.option("--tags-path", type=click.Path(path_type=Path), default=Path("tags"), show_default=True)
    @click.option("--heritage-db", "heritage_db_path", type=click.Path(path_type=Path), default=DEFAULT_INDEX_DB, show_default=True)
    @click.option("--knowledge-dir", type=click.Path(path_type=Path), default=Path("knowledge"), show_default=True)
    @click.option("--wiki-dir", type=click.Path(path_type=Path), default=Path("wiki"), show_default=True)
    @click.option("--auto-rebuild/--no-auto-rebuild", default=False, show_default=True)
    @click.option("--verify-frontmatter/--no-verify-frontmatter", default=True, show_default=True)
    @click.option("--strict", is_flag=True, help="Exit with code 2 when workbench is not operational.")
    @click.option("--json", "as_json", is_flag=True, help="Emit readiness report as JSON.")
    def readiness(
        db_path: Path, lake_dir: Path, rom_catalog_path: Path, tags_path: Path,
        heritage_db_path: Path, knowledge_dir: Path, wiki_dir: Path,
        auto_rebuild: bool, verify_frontmatter: bool, strict: bool, as_json: bool,
    ) -> None:
        """Inspect catalog, telemetry lake, ROM manifests, and heritage index readiness."""
        try:
            report = check_workbench_readiness(
                db_path=db_path, lake_dir=lake_dir, rom_catalog_path=rom_catalog_path,
                tags_path=tags_path, heritage_db_path=heritage_db_path,
                knowledge_dir=knowledge_dir, wiki_dir=wiki_dir,
                auto_rebuild=auto_rebuild, verify_frontmatter=verify_frontmatter,
            )
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(json.dumps(report, indent=2, sort_keys=True) if as_json else format_readiness_text(report))
        if strict and not report.get("operational"):
            raise SystemExit(2)


def _register_issues_list_and_create(issues_group: click.Group) -> None:
    @issues_group.command(name="list")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--car-id", default=None, help="Filter by car_id.")
    @click.option("--session-id", default=None, help="Filter by session_id.")
    @click.option("--status", default=None, help="Filter by issue status.")
    @click.option("--severity", default=None, help="Filter by issue severity.")
    @click.option("--limit", type=int, default=None, help="Maximum issues to return.")
    @click.option("--json", "as_json", is_flag=True, help="Emit matching issues as JSON.")
    def issues_list(
        db_path: Path, car_id: str | None, session_id: str | None,
        status: str | None, severity: str | None, limit: int | None, as_json: bool,
    ) -> None:
        """List persisted tuning issues."""
        try:
            rows = (
                list_tuning_issues(db_path, car_id=car_id, session_id=session_id, status=status, severity=severity, limit=limit)
                if db_path.is_file() else []
            )
        except (sqlite3.Error, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        if as_json:
            click.echo(json.dumps({"count": len(rows), "issues": rows}, indent=2, sort_keys=True))
            return
        click.echo("No tuning issues found." if not rows else "\n".join(
            f"{r['issue_id']} | {r['status']} | {r['severity']} | car={r['car_id']} | title={r['title']}" for r in rows
        ))

    @issues_group.command(name="create")
    @click.option("--car-id", required=True, help="Vehicle identifier.")
    @click.option("--issue-id", default=None, help="Optional explicit issue identifier.")
    @click.option("--session-id", default=None, help="Optional linked telemetry session_id.")
    @click.option("--build-id", default=None, help="Optional calibration build identifier.")
    @click.option("--severity", default=None, help="Issue severity (INFO, CAUTION, CRITICAL).")
    @click.option("--category", default=None, help="Issue category tag.")
    @click.option("--status", default=None, help="Initial status (OPEN, IN_PROGRESS, VERIFIED_FIXED, WONT_FIX).")
    @click.option("--title", default=None, help="Short issue title.")
    @click.option("--description", default=None, help="Detailed issue description.")
    @click.option("--note", default=None, help="Additional note appended to description.")
    @click.option("--rpm-min", type=float, default=None, help="Minimum RPM bound.")
    @click.option("--rpm-max", type=float, default=None, help="Maximum RPM bound.")
    @click.option("--map-min-kpa", type=float, default=None, help="Minimum MAP (kPa) bound.")
    @click.option("--map-max-kpa", type=float, default=None, help="Maximum MAP (kPa) bound.")
    @click.option("--create-bookmark/--no-create-bookmark", default=False, show_default=True)
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--json", "as_json", is_flag=True, help="Emit created issue record as JSON.")
    def issues_create(
        car_id: str, issue_id: str | None, session_id: str | None, build_id: str | None,
        severity: str | None, category: str | None, status: str | None, title: str | None,
        description: str | None, note: str | None, rpm_min: float | None, rpm_max: float | None,
        map_min_kpa: float | None, map_max_kpa: float | None, create_bookmark: bool, db_path: Path, as_json: bool,
    ) -> None:
        """Create a persisted tuning issue."""
        try:
            result = create_or_update_tuning_issue(
                db_path, issue_id=issue_id, car_id=car_id, session_id=session_id, build_id=build_id,
                severity=severity, category=category, status=status, title=title, description=description,
                note=note, rpm_min=rpm_min, rpm_max=rpm_max, map_min_kpa=map_min_kpa,
                map_max_kpa=map_max_kpa, create_bookmark=create_bookmark,
            )
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            raise click.ClickException(str(exc)) from exc
        _emit_issue_result(result, as_json)


def _register_issues_update(issues_group: click.Group) -> None:
    @issues_group.command(name="update")
    @click.option("--issue-id", required=True, help="Existing issue identifier to update.")
    @click.option("--status", default=None, help="Updated status (OPEN, IN_PROGRESS, VERIFIED_FIXED, WONT_FIX).")
    @click.option("--severity", default=None, help="Updated severity (INFO, CAUTION, CRITICAL).")
    @click.option("--title", default=None, help="Updated issue title.")
    @click.option("--description", default=None, help="Replacement description.")
    @click.option("--note", default=None, help="Note appended to existing or updated description.")
    @click.option("--rpm-min", type=float, default=None, help="Updated minimum RPM bound.")
    @click.option("--rpm-max", type=float, default=None, help="Updated maximum RPM bound.")
    @click.option("--map-min-kpa", type=float, default=None, help="Updated minimum MAP (kPa) bound.")
    @click.option("--map-max-kpa", type=float, default=None, help="Updated maximum MAP (kPa) bound.")
    @click.option("--db-path", type=click.Path(path_type=Path), default=DEFAULT_CATALOG_DB, show_default=True)
    @click.option("--json", "as_json", is_flag=True, help="Emit updated issue record as JSON.")
    def issues_update(
        issue_id: str, status: str | None, severity: str | None, title: str | None,
        description: str | None, note: str | None, rpm_min: float | None, rpm_max: float | None,
        map_min_kpa: float | None, map_max_kpa: float | None, db_path: Path, as_json: bool,
    ) -> None:
        """Update an existing persisted tuning issue."""
        try:
            if get_tuning_issue(issue_id, db_path) is None:
                raise click.ClickException(f"Tuning issue not found: {issue_id}")
            result = create_or_update_tuning_issue(
                db_path, issue_id=issue_id, severity=severity, status=status, title=title,
                description=description, note=note, rpm_min=rpm_min, rpm_max=rpm_max,
                map_min_kpa=map_min_kpa, map_max_kpa=map_max_kpa,
            )
        except click.ClickException:
            raise
        except (ValueError, TypeError, OSError, sqlite3.Error) as exc:
            raise click.ClickException(str(exc)) from exc
        _emit_issue_result(result, as_json)


def _register_issues_commands(telemetry_group: click.Group) -> None:
    @telemetry_group.group(name="issues", help="List, create, and update persisted tuning issues.")
    def issues() -> None:
        """Manage persisted tuning_issues records in the SQLite catalog."""

    _register_issues_list_and_create(issues)
    _register_issues_update(issues)


def register_workbench_commands(main_group: click.Group, telemetry_group: click.Group) -> None:
    """Register `brief`, `readiness`, and `telemetry issues` commands on the CLI."""
    _register_brief_commands(main_group)
    _register_readiness_command(main_group)
    _register_issues_commands(telemetry_group)
