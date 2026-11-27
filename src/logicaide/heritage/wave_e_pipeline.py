"""Wave E crawl & web normalization pipeline facade for LogicAIDE cleanroom heritage."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Sequence
import urllib.parse

from bs4 import BeautifulSoup

from logicaide.heritage.crawl4ai_client import validate_heritage_url
from logicaide.heritage.doc_curator import QuarantinedPathError, check_quarantine
from logicaide.heritage.practitioner_graph import (
    DEFAULT_ROSTER_PATH, build_practitioner_authority_graph, classify_thread_trust_tier,
)
from logicaide.heritage.warc_vault import iter_warc_html_records
from logicaide.heritage.wave_e_crawl import (
    DEFAULT_FIGURES_ROOT, DEFAULT_MANIFEST_PATH, DEFAULT_QUEUE_PATH, DEFAULT_WARC_ROOT,
    DODGEGARAGE_SEEDS, MINIMOPAR_SEEDS, TURBOMOPAR_SEED, WAVE_E_SOURCES,
    _reject_symlink, _unwrap_wayback_url, run_wave_e_crawl,
)
from logicaide.heritage.web_normalizer import (
    enforce_heritage_safety, extract_clean_markdown, parse_forum_html, scrub_all_pii, stitch_forum_threads,
)

DEFAULT_WEB_MD_DIR = Path("data/heritage_raw/web_md")
_FORUM_URL_RE = re.compile(r"turbo-mopar\.com/forums", re.I)
_EXCLUDED_LOCAL_DIRS = frozenset({"quarantine_pii_do_not_ingest", "archive_raw_html", "portmon"})
_FORUM_DOM_RE = re.compile(
    r"(?:vb_postbit|post_message_|bigusername|postprofile|postbody|showthread|viewtopic|originally\s+posted\s+by|class=[\"'][^\"']*\bforum-post\b)",
    re.I,
)


def _collect_input_html_records(
    warc_root: Path, local_archive_dirs: Sequence[Path], errors: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    """Gather HTML records from WARCs under warc_root and safe local HTML dirs."""
    records, warc_count, local_count = [], 0, 0
    if warc_root.is_dir():
        for root, dirs, files in os.walk(warc_root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                fpath = Path(root) / name
                if fpath.is_symlink() or not name.lower().endswith((".warc", ".warc.gz")):
                    continue
                rel = fpath.relative_to(warc_root)
                tag = rel.parts[0] if len(rel.parts) > 1 else "warc"
                for rec in iter_warc_html_records(fpath):
                    if int(rec.get("status_code") or 200) < 400 and str(rec.get("text") or "").strip():
                        warc_count += 1
                        records.append({
                            "source_tag": tag, "url": str(rec.get("url") or ""),
                            "archived_at": str(rec.get("warc_date") or ""), "html": str(rec.get("text") or ""),
                            "sha256": str(rec.get("sha256") or ""),
                        })

    for ldir in local_archive_dirs:
        if not ldir.exists():
            errors.append({"path": str(ldir), "error": "Local archive directory does not exist"})
            continue
        for root, dirs, files in os.walk(ldir, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not d.lower().endswith("_files") and d.lower() not in _EXCLUDED_LOCAL_DIRS)
            for name in sorted(files):
                fpath = Path(root) / name
                low_parts = {p.lower() for p in fpath.parts}
                if fpath.is_symlink() or fpath.suffix.lower() not in {".htm", ".html"} or (low_parts & _EXCLUDED_LOCAL_DIRS) or any(p.endswith("_files") for p in low_parts):
                    continue
                try:
                    html = check_quarantine(fpath).read_text(encoding="utf-8", errors="ignore")
                except (QuarantinedPathError, OSError) as exc:
                    errors.append({"path": str(fpath), "error": scrub_all_pii(str(exc))})
                    continue
                rel = fpath.relative_to(ldir)
                raw_tag = rel.parts[0] if len(rel.parts) > 1 else (ldir.name or "local")
                local_count += 1
                records.append({
                    "source_tag": re.sub(r"[^a-z0-9_-]+", "-", raw_tag.lower()).strip("-") or "local",
                    "url": f"http://local-archive.invalid/{raw_tag}/{fpath.name}", "archived_at": "", "html": html,
                    "sha256": hashlib.sha256(html.encode("utf-8", errors="ignore")).hexdigest()[:12],
                })
    return records, warc_count, local_count


def _unique_md_path(dest_dir: Path, slug_seed: str, used_paths: set[Path], sha_hint: str = "") -> Path:
    """Resolve a deterministic collision-safe .md output path within a single run."""
    slug = re.sub(r"[^a-z0-9]+", "-", slug_seed.lower()).strip("-")[:80] or "page"
    candidate = dest_dir / f"{slug}.md"
    if candidate in used_paths:
        suffix = sha_hint[:8] if sha_hint else hashlib.sha256(slug_seed.encode("utf-8")).hexdigest()[:8]
        candidate, idx = dest_dir / f"{slug}-{suffix}.md", 2
        while candidate in used_paths:
            candidate = dest_dir / f"{slug}-{suffix}-{idx}.md"
            idx += 1
    used_paths.add(candidate)
    return candidate


def _write_static_markdown(rec: dict[str, Any], output_dir: Path, used_paths: set[Path], errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Normalize a static/article HTML record to cleanroom Markdown with provenance header."""
    url, html, tag = rec["url"], rec["html"], rec["source_tag"]
    ts, inner = _unwrap_wayback_url(url)
    orig_url, archived_at = (inner if ts else url), (rec["archived_at"] or ts or "unknown")
    soup = BeautifulSoup(html, "lxml")
    raw_heading = (soup.title.get_text(" ", strip=True) if soup.title else "") or orig_url or "Heritage Page"
    raw_auth = (
        str(meta_author.get("content") or "").strip()
        if (meta_author := soup.find("meta", attrs={"name": re.compile(r"^author$", re.I)}))
        else ""
    )
    try:
        heading = enforce_heritage_safety(raw_heading, label=f"title:{orig_url}")
        clean_author = enforce_heritage_safety(raw_auth, label=f"meta_author:{orig_url}") if raw_auth else ""
        body = extract_clean_markdown(html, label=orig_url or tag)
        full_md = enforce_heritage_safety(
            f"# {heading}\n\n- **Source Tag**: {tag}\n- **Original URL**: {orig_url}\n- **Archived At**: {archived_at}\n\n{body}\n",
            label=f"static:{orig_url}",
        )
    except (ValueError, RuntimeError) as exc:
        errors.append({"url": url, "error": scrub_all_pii(str(exc)), "error_type": type(exc).__name__})
        return None
    dest_dir = output_dir / tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    _unique_md_path(dest_dir, urllib.parse.urlparse(orig_url).path.strip("/") or heading, used_paths, rec.get("sha256", "")).write_text(full_md, encoding="utf-8")
    return {"author": clean_author, "title": heading, "body": body} if clean_author else None


def run_wave_e_parse_web(
    *, warc_root: Path | str = DEFAULT_WARC_ROOT, output_dir: Path | str = DEFAULT_WEB_MD_DIR,
    roster_path: Path | str = DEFAULT_ROSTER_PATH, local_archive_dirs: Sequence[Path | str] = (),
    pin_handles: Sequence[str] = (), mute_handles: Sequence[str] = (),
) -> dict[str, Any]:
    """Normalize WARC + safe local HTML records into cleanroom Markdown and trust-tiered threads."""
    wroot, out_dir = _reject_symlink(warc_root, label="warc_root"), _reject_symlink(output_dir, label="output_dir")
    rpath, local_dirs = _reject_symlink(roster_path, label="roster_path"), [_reject_symlink(d, label="local_archive_dir") for d in local_archive_dirs]
    errors: list[dict[str, Any]] = []
    records, warc_count, local_count = _collect_input_html_records(wroot, local_dirs, errors)
    out_dir.mkdir(parents=True, exist_ok=True)

    forum_pages, tag_by_tid, tag_by_title, authorable_docs, used_paths, static_written = [], {}, {}, [], set(), 0
    for rec in records:
        try:
            parsed = parse_forum_html(
                rec["html"], url=rec["url"], url_validator=lambda u: validate_heritage_url(u, allow_local_archive=True),
            )
        except (ValueError, RuntimeError) as exc:
            errors.append({"url": rec["url"], "error": scrub_all_pii(str(exc)), "error_type": type(exc).__name__})
            continue
        is_forum = bool(parsed.get("posts")) and (
            bool(_FORUM_URL_RE.search(rec["url"])) or "/forums/" in rec["url"].lower()
            or parsed.get("forum_engine") in {"vbulletin", "phpbb"} or bool(_FORUM_DOM_RE.search(rec["html"]))
        )
        if is_forum:
            forum_pages.append(parsed)
            if tid := str(parsed.get("thread_id") or "").strip():
                tag_by_tid.setdefault(tid, rec["source_tag"])
            if ttl := str(parsed.get("thread_title") or "").strip().lower():
                tag_by_title.setdefault(ttl, rec["source_tag"])
        else:
            err_before = len(errors)
            if (auth_doc := _write_static_markdown(rec, out_dir, used_paths, errors)) is not None:
                authorable_docs.append(auth_doc)
            static_written += int(len(errors) == err_before)

    stitched = stitch_forum_threads(forum_pages) if forum_pages else []
    for thread in stitched:
        for post in thread.get("posts") or ():
            if isinstance(post, dict) and not post.get("body") and post.get("body_text"):
                post["body"] = post["body_text"]
    roster = build_practitioner_authority_graph(
        stitched + authorable_docs, roster_path=rpath, pin_handles=pin_handles, mute_handles=mute_handles, save_roster=True,
    )
    forum_written, tier2_count, tier3_count = 0, 0, 0
    for thread in stitched:
        tier = classify_thread_trust_tier(thread, roster)
        tier2_count, tier3_count = tier2_count + (tier == "Tier 2"), tier3_count + (tier != "Tier 2")
        tid, ttl = str(thread.get("thread_id") or "").strip(), str(thread.get("thread_title") or "").strip().lower()
        tag = tag_by_tid.get(tid) or tag_by_title.get(ttl) or "turbomopar"
        tdir = out_dir / tag / "threads"
        tdir.mkdir(parents=True, exist_ok=True)
        badge = f"> **Trust Tier**: {tier} | **Source Tag**: {tag} | **Thread ID**: {tid or 'n/a'} | **Pages**: {thread.get('page_count', 1)}"
        rendered = enforce_heritage_safety(f"{badge}\n\n{thread.get('summary_markdown') or ''}\n", label=f"thread:{tid or ttl}")
        _unique_md_path(tdir, tid or ttl or "thread", used_paths).write_text(rendered, encoding="utf-8")
        forum_written += 1

    summary: dict[str, Any] = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "warc_records": warc_count, "local_files": local_count, "static_docs_written": static_written,
        "forum_threads_written": forum_written, "tier2_threads": tier2_count, "tier3_threads": tier3_count,
        "roster_path": str(rpath), "practitioner_counts": dict(roster.get("summary") or {}), "errors": errors,
    }
    (out_dir / "parse_manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


__all__ = [
    "DEFAULT_FIGURES_ROOT", "DEFAULT_MANIFEST_PATH", "DEFAULT_QUEUE_PATH", "DEFAULT_WARC_ROOT", "DEFAULT_WEB_MD_DIR",
    "DODGEGARAGE_SEEDS", "MINIMOPAR_SEEDS", "TURBOMOPAR_SEED", "WAVE_E_SOURCES",
    "run_wave_e_crawl", "run_wave_e_parse_web",
]
