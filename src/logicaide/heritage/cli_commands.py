"""Wave D CLI subcommand registrations for ``logicaide heritage``."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

import click

_HANDLED_ERRORS = (sqlite3.Error, RuntimeError, FileNotFoundError, KeyError, ValueError, TypeError, OSError)


def _emit_json(payload: dict[str, Any]) -> None:
    """Print a dictionary as deterministic sorted JSON."""
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


def _format_snippet(body: str, max_chars: int = 240) -> str:
    """Return a single-line bounded snippet from markdown body text."""
    collapsed = " ".join((body or "").split())
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[: max_chars - 3].rstrip()}..."


def _format_parent_entry(item: dict[str, Any]) -> list[str]:
    """Format a parent chunk search hit into human-readable lines."""
    ecu_scope = ", ".join(item.get("ecu_scope") or []) or "all"
    header = (
        f"- [{item.get('trust_tier', 'Unknown')}] {item.get('doc_id', '')}"
        f" ({item.get('parent_id', '')}) | ECU: {ecu_scope}"
    )
    if item.get("score") is not None:
        header = f"{header} | score={float(item['score']):.4f}"
    lines = [header, f"  Section: {item.get('section_path', '')}"]
    citation, page_ref = item.get("source_citation") or "", item.get("page_ref") or ""
    if citation or page_ref:
        lines.append(f"  Source: {citation}{f' ({page_ref})' if page_ref else ''}")
    snippet = _format_snippet(str(item.get("body_markdown") or ""))
    if snippet:
        lines.append(f"  Snippet: {snippet}")
    return lines


def _print_provenance_card(result: dict[str, Any]) -> None:
    """Format a provenance card result for terminal output."""
    ecu_scope = ", ".join(result.get("ecu_scope") or []) or "all"
    engine_scope = ", ".join(result.get("engine_scope") or []) or "all"
    click.echo(f"Document: {result.get('doc_id')} (matched_by={result.get('matched_by')})")
    click.echo(f"Tier: {result.get('trust_tier')} | ECU: {ecu_scope} | Engine: {engine_scope}")
    click.echo(f"Citation: {result.get('source_citation')} | Page: {result.get('page_ref') or 'n/a'}")
    if result.get("warc_capture_date"):
        click.echo(f"Capture Date: {result['warc_capture_date']}")
    click.echo(f"Sections ({result.get('section_count', 0)}):")
    for sec in result.get("sections", []):
        click.echo(
            f"- {sec.get('parent_id')}: {sec.get('section_path')} "
            f"[sha256={sec.get('sha256')}, mermaid={sec.get('mermaid_verification')}]"
        )


def _print_query_human(result: dict[str, Any]) -> None:
    """Render any ``heritage query`` result dict for human readers."""
    if not result.get("matched"):
        click.echo(result.get("message", "No Verified Source Match"))
        return
    if "sections" in result and "matched_by" in result:
        _print_provenance_card(result)
        return
    if result.get("pin_label"):
        click.echo(f"{result['pin_label']} | status={result.get('status')}")
    for note in result.get("safety_notes") or []:
        click.echo(f"Safety Note: {note}")
    if "manifest_matches" in result or "heritage_results" in result:
        click.echo(f"SCI Lookup: {result.get('query')} | status={result.get('status')}")
        for match in result.get("manifest_matches") or []:
            click.echo(
                f"- ROM {match.get('rom_id')}: {match.get('var_name')} "
                f"offset={match.get('offset')} table={match.get('sci_table')}"
            )
        for item in result.get("heritage_results") or []:
            for line in _format_parent_entry(item):
                click.echo(line)
        return
    for item in result.get("results") or []:
        for line in _format_parent_entry(item):
            click.echo(line)


def _print_diagram_human(result: dict[str, Any]) -> None:
    """Render a ``heritage diagram`` response for human readers."""
    if not result.get("matched"):
        click.echo(result.get("message", "No Verified Source Match"))
        return
    for diagram in result.get("diagrams") or []:
        ecu_scope = ", ".join(diagram.get("ecu_scope") or []) or "all"
        status_note = "QUARANTINED" if diagram.get("quarantined") else "VERIFIED"
        click.echo(
            f"[{diagram.get('trust_tier', 'Unknown')}] {diagram.get('doc_id')} "
            f"({diagram.get('parent_id')}) | {status_note} | ECU: {ecu_scope}"
        )
        click.echo(f"Section: {diagram.get('section_path', '')}")
        page_ref = diagram.get("page_ref")
        click.echo(
            f"Source: {diagram.get('source_citation', '')}"
            f"{f' ({page_ref})' if page_ref else ''}"
        )
        if diagram.get("mermaid_source"):
            click.echo("```mermaid")
            click.echo(diagram["mermaid_source"])
            click.echo("```")
        walkthrough = (diagram.get("body_markdown") or "").strip()
        if walkthrough:
            click.echo(walkthrough)
        click.echo("")


def _run_build_index(
    knowledge_dir: Path,
    wiki_dir: Path,
    db_path: Path,
    verify_frontmatter: bool,
    as_json: bool,
) -> None:
    """Build the heritage SQLite RAG index and emit JSON or summary output."""
    from logicaide.heritage.rag_index import build_heritage_index

    try:
        result = build_heritage_index(
            knowledge_dir=knowledge_dir,
            wiki_dir=wiki_dir,
            db_path=db_path,
            verify_frontmatter=verify_frontmatter,
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit_json(result)
        return
    click.echo(
        f"Built heritage index: {result['parent_count']} parents, "
        f"{result['child_count']} children, backend={result['vector_backend']}, "
        f"verified_mermaid={result['verified_mermaid_count']}, "
        f"quarantined_mermaid={result['quarantined_mermaid_count']} -> {result['db_path']}"
    )


def _with_index_options(fn: Callable[..., None]) -> Callable[..., None]:
    """Attach shared index-building Click options to a command callback."""
    options = [
        click.option("--json", "as_json", is_flag=True, help="Emit index build summary as JSON."),
        click.option("--verify-frontmatter/--no-verify-frontmatter", default=True, show_default=True, help="Require valid heritage YAML frontmatter on indexed Markdown files."),
        click.option("--db-path", type=click.Path(path_type=Path), default=Path("data/heritage_index.sqlite"), show_default=True, help="Destination SQLite index path."),
        click.option("--wiki-dir", type=click.Path(path_type=Path), default=Path("wiki"), show_default=True, help="Directory containing heritage wiki Markdown pages."),
        click.option("--knowledge-dir", type=click.Path(path_type=Path), default=Path("knowledge"), show_default=True, help="Directory containing cleanroom heritage knowledge Markdown pages."),
    ]
    for opt in options:
        fn = opt(fn)
    return fn


@click.command(name="parse-docs")
@click.option("--input", "input_path", type=click.Path(path_type=Path), default=Path("private_references"), show_default=True, help="Input PDF file or directory.")
@click.option("--output-md-dir", type=click.Path(path_type=Path), default=Path("data/heritage_raw/pdfs_md"), show_default=True, help="Directory for extracted Markdown outputs.")
@click.option("--figures-dir", type=click.Path(path_type=Path), default=Path("data/heritage_raw/figures"), show_default=True, help="Directory for rendered PDF figure PNGs.")
@click.option("--dpi", type=click.IntRange(min=1), default=300, show_default=True, help="Render DPI for extracted PDF figures.")
@click.option("--max-pages", type=click.IntRange(min=1), default=None, help="Optional maximum page count per PDF.")
@click.option("--figures/--no-figures", default=True, show_default=True, help="Render page figures during extraction.")
@click.option("--verify-mermaid/--no-verify-mermaid", default=False, show_default=True, help="Run Mermaid verification checks on extracted diagrams.")
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Optional maximum number of PDFs to process.")
@click.option("--json", "as_json", is_flag=True, help="Emit extraction manifest as JSON.")
def parse_docs_cmd(
    input_path: Path,
    output_md_dir: Path,
    figures_dir: Path,
    dpi: int,
    max_pages: int | None,
    figures: bool,
    verify_mermaid: bool,
    limit: int | None,
    as_json: bool,
) -> None:
    """Discover, deduplicate, and extract heritage PDF documents to Markdown."""
    from logicaide.heritage.pdf_extractor import run_parse_docs

    try:
        result = run_parse_docs(
            input_path=input_path,
            output_md_dir=output_md_dir,
            figures_dir=figures_dir,
            render_dpi=dpi,
            max_pages=max_pages,
            render_figures=figures,
            verify_mermaid=verify_mermaid,
            limit=limit,
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit_json(result)
        return
    click.echo(
        f"Parsed {result['processed_count']} PDFs "
        f"(discovered={result['discovered_count']}, duplicates={result['duplicate_count']}, "
        f"excluded={result['excluded_count']}) -> {output_md_dir / 'manifest.json'}"
    )


@click.command(name="build-index")
@_with_index_options
def build_index_cmd(
    knowledge_dir: Path, wiki_dir: Path, db_path: Path, verify_frontmatter: bool, as_json: bool
) -> None:
    """Build or rebuild the offline SQLite heritage RAG index."""
    _run_build_index(knowledge_dir, wiki_dir, db_path, verify_frontmatter, as_json)


@click.command(name="index")
@_with_index_options
def index_alias_cmd(
    knowledge_dir: Path, wiki_dir: Path, db_path: Path, verify_frontmatter: bool, as_json: bool
) -> None:
    """Alias for ``heritage build-index``."""
    _run_build_index(knowledge_dir, wiki_dir, db_path, verify_frontmatter, as_json)


@click.command(name="query")
@click.argument("query_text", required=False, default="")
@click.option("--ecu", type=str, default=None, help="Filter results to an ECU family scope.")
@click.option("--min-tier", type=click.IntRange(1, 3), default=3, show_default=True, help="Maximum trust tier number to include (1..3).")
@click.option("--limit", type=click.IntRange(min=1), default=8, show_default=True, help="Maximum number of search results.")
@click.option("--pin", type=str, default=None, help="Look up a 60-way connector pin from Tier 1/2 sources.")
@click.option("--sci", type=str, default=None, help="Cross-reference an SCI offset or variable across ROM manifests and Tier 1/2 docs.")
@click.option("--provenance", type=str, default=None, help="Fetch provenance card for a doc_id or parent_id.")
@click.option("--rom-id", type=str, default=None, help="Optional ROM manifest ID for --sci lookups.")
@click.option("--sci-table", type=str, default=None, help="Optional SCI table selector for --sci lookups.")
@click.option("--catalog", type=click.Path(path_type=Path), default=Path("rom/manifests/catalog.json"), show_default=True, help="ROM manifest catalog path for --sci lookups.")
@click.option("--db-path", type=click.Path(path_type=Path), default=Path("data/heritage_index.sqlite"), show_default=True, help="Heritage SQLite RAG index path.")
@click.option("--json", "as_json", is_flag=True, help="Emit query result as JSON.")
def query_heritage_cmd(
    query_text: str,
    ecu: str | None,
    min_tier: int,
    limit: int,
    pin: str | None,
    sci: str | None,
    provenance: str | None,
    rom_id: str | None,
    sci_table: str | None,
    catalog: Path,
    db_path: Path,
    as_json: bool,
) -> None:
    """Query the offline heritage RAG index, 60-way pinout, SCI offset, or provenance."""
    from logicaide.heritage import rag_query

    has_pin, has_sci = bool(pin and pin.strip()), bool(sci and sci.strip())
    has_prov, has_text = bool(provenance and provenance.strip()), bool(query_text.strip())
    if not (has_text or has_pin or has_sci or has_prov):
        raise click.UsageError("Provide QUERY_TEXT or one of --pin, --sci, or --provenance.")

    try:
        if has_pin:
            result = rag_query.lookup_60way_pin(pin or "", ecu=ecu, db_path=db_path)
        elif has_sci:
            result = rag_query.lookup_sci_offset_hybrid(
                sci or "", rom_id=rom_id, sci_table=sci_table, catalog_path=catalog, db_path=db_path
            )
        elif has_prov:
            result = rag_query.get_provenance_card(provenance or "", db_path=db_path)
        else:
            result = rag_query.search_heritage_docs(
                query_text, db_path=db_path, limit=limit, ecu=ecu, min_tier=min_tier
            )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit_json(result)
        return
    _print_query_human(result)


@click.command(name="diagram")
@click.option("--doc", "doc_id", type=str, default=None, help="Filter verified schematics by doc_id.")
@click.option("--parent-id", type=str, default=None, help="Filter verified schematics by parent_id.")
@click.option("--query", type=str, default=None, help="Search query to rank matching schematics.")
@click.option("--pending-review/--no-pending-review", default=False, show_default=True, help="Include quarantined NEEDS_HUMAN_REVIEW schematics.")
@click.option("--ecu", type=str, default=None, help="Filter schematics by ECU family scope.")
@click.option("--min-tier", type=click.IntRange(1, 3), default=3, show_default=True, help="Maximum trust tier number to include (1..3).")
@click.option("--db-path", type=click.Path(path_type=Path), default=Path("data/heritage_index.sqlite"), show_default=True, help="Heritage SQLite RAG index path.")
@click.option("--json", "as_json", is_flag=True, help="Emit diagram result as JSON.")
def diagram_heritage_cmd(
    doc_id: str | None,
    parent_id: str | None,
    query: str | None,
    pending_review: bool,
    ecu: str | None,
    min_tier: int,
    db_path: Path,
    as_json: bool,
) -> None:
    """Retrieve verified Mermaid schematics and walkthroughs from the heritage index."""
    from logicaide.heritage import rag_query

    try:
        result = rag_query.get_verified_schematic(
            doc_id=doc_id,
            parent_id=parent_id,
            query=query,
            pending_review=pending_review,
            ecu=ecu,
            min_tier=min_tier,
            db_path=db_path,
        )
    except _HANDLED_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        _emit_json(result)
        return
    _print_diagram_human(result)


def register_heritage_commands(heritage_group: click.Group) -> None:
    """Register Wave D heritage subcommands onto ``heritage_group``."""
    for cmd in (parse_docs_cmd, build_index_cmd, index_alias_cmd, query_heritage_cmd, diagram_heritage_cmd):
        heritage_group.add_command(cmd)
