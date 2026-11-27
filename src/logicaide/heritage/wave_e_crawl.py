"""Wave E bounded crawl stage across MiniMopar, DodgeGarage, Turbo-Mopar Wayback, and pre-sale Allpar."""

from __future__ import annotations

import heapq
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Sequence

from bs4 import BeautifulSoup
import httpx

from logicaide.heritage.crawl4ai_client import (
    AntiBotBlockedError, HijackedDomainError, _host_matches_domain, validate_heritage_url,
)
from logicaide.heritage.discovery import build_cdx_query_url, build_memento_url, fetch_cdx_snapshots
from logicaide.heritage.discovery_wave_e import (
    ALLPAR_CUTOFF_TO, ALLPAR_PREFIXES, TURBO_MOPAR_CUTOFF_TO, validate_wave_e_query_pattern,
)
from logicaide.heritage.warc_vault import HeritageWarcVault, iter_warc_html_records
from logicaide.heritage.web_normalizer import extract_frameset_urls, scrub_all_pii

WAVE_E_SOURCES: tuple[str, ...] = ("minimopar", "thedodgegarage", "turbomopar", "allpar")
DEFAULT_QUEUE_PATH = Path("data/heritage_raw/discovery_queue/wave_e_queue.json")
DEFAULT_WARC_ROOT = Path("data/heritage_raw/warc")
DEFAULT_FIGURES_ROOT = Path("data/heritage_raw/figures")
DEFAULT_MANIFEST_PATH = Path("private_references/_INDEX_AND_ROUTING_MANIFEST.md")

MINIMOPAR_SEEDS = tuple(f"http://www.minimopar.net{p}" for p in ("/", "/ecu/", "/fault/", "/perf/", "/engine.html", "/drivetrain.html", "/electrical.html", "/oilfilters/"))
DODGEGARAGE_SEEDS = tuple(f"http://www.thedodgegarage.com{p}" for p in ("/", "/index_left.html", "/index_mainwindow.html", "/turbo.html", "/friends_gus_m.html", "/trans_guide.html"))
TURBOMOPAR_SEED = "https://sites.google.com/site/mpscilink"
_DODGEGARAGE_TECH_KEYWORDS = ("ecu", "smec", "sbec", "6803", "peftbl", "drb", "sci", "turbo", "gus", "trans", "a555", "a568", "head", "calc", "tech", "inject", "ignition", "boost", "knock")
_ALLPAR_TECH_KEYWORDS = ("smec", "sbec", "glhs", "shelby", "daytona", "omni", "turbo", "trans", "a555", "a568", "te04h", "vnt", "16v", "lotus", "drb", "sci", "2.2", "2.5")
_SKIP_EXTENSIONS = frozenset(
    ".zip .pdf .exe .jpg .jpeg .gif .png .bmp .ico .svg .css .js .xml .mp3 .mp4 .wav .avi .mov .wmv .doc .docx .xls .xlsx .ppt .tar .gz .rar .7z .bin .rom .s19 .hex .asm .c .h .dll .msi".split()
)
_WAYBACK_RE = re.compile(r"^/web/(\d{4,14})(?:[a-z_]+)?/(.+)$", re.I)
_VB_SESSION_RE = re.compile(r"^[0-9a-f]{32}$", re.I)
_TM_ARCHIVE_RE = re.compile(r"(?:/forums/archive/|/(?:f|t)-\d+[^/]*\.html$|/showthread\.php)", re.I)


def _reject_symlink(path: Path | str | None, *, label: str = "path") -> Path | None:
    if path is not None and Path(path).is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    return Path(path) if path is not None else None


def _is_non_html(url: str) -> bool:
    return Path(urllib.parse.urlparse(url).path.lower()).suffix in _SKIP_EXTENSIONS


def _new_stats() -> dict[str, Any]:
    return {"attempted_count": 0, "captured_count": 0, "deduplicated_count": 0, "blocked_count": 0, "error_count": 0, "urls": []}


def _validate_or_block(ctx: dict[str, Any], stats: dict[str, Any], source: str, url: str, *, allow_live_turbomopar: bool = False) -> bool:
    """Validate heritage URL; log HijackedDomainError/AntiBotBlockedError under blocked."""
    try:
        validate_heritage_url(url, allow_live_turbomopar=allow_live_turbomopar)
        return True
    except (HijackedDomainError, AntiBotBlockedError) as exc:
        stats["blocked_count"] += 1
        ctx["blocked"].append({"source": source, "url": url, "reason": str(exc), "error_type": type(exc).__name__})
        return False
    except ValueError:
        return False


def _unwrap_wayback_url(url: str) -> tuple[str | None, str]:
    """Return (timestamp, inner_url) for a Wayback URL, or (None, url) otherwise."""
    parsed = urllib.parse.urlparse(url.strip())
    if not _host_matches_domain(parsed.hostname or "", "web.archive.org") or not (m := _WAYBACK_RE.match(parsed.path or "")):
        return None, url.strip()
    ts, inner = m.group(1), f"{m.group(2)}?{parsed.query}" if parsed.query else m.group(2)
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", inner):
        inner = f"http://{inner.lstrip('/')}"
    ip = urllib.parse.urlparse(inner)
    return ts, urllib.parse.urlunparse(ip._replace(netloc=re.sub(r":(?:80|443)$", "", ip.netloc), fragment=""))


def _normalize_url(url: str, *, strip_vb_session: bool = False) -> tuple[str | None, str, str]:
    """Return (timestamp, normalized_inner_url, normalized_memento_or_url)."""
    ts, inner = _unwrap_wayback_url(url)
    p = urllib.parse.urlparse(inner)
    if strip_vb_session and p.query:
        parts = [seg for seg in p.query.split("&") if seg and not (seg.lower().startswith("s=") and _VB_SESSION_RE.fullmatch(seg.split("=", 1)[1].strip()))]
        inner = urllib.parse.urlunparse(p._replace(query="&".join(parts), fragment=""))
    else:
        inner = urllib.parse.urlunparse(p._replace(fragment=""))
    return (ts, inner, build_memento_url(ts, inner, raw=True)) if ts is not None else (None, inner, inner)


def _extract_hrefs(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    return [raw for tag in soup.find_all(["a", "area"]) if (raw := str(tag.get("href") or "").strip()) and not raw.startswith(("#", "javascript:", "mailto:", "data:", "tel:"))]


def _load_captured_html(result: dict[str, Any], warc_cache: dict[Path, dict[str, str]]) -> str:
    """Retrieve HTML for a captured/deduplicated record from result or its WARC."""
    if isinstance(result.get("html"), str):
        return result["html"]
    warc_raw, sha = result.get("warc_path"), str(result.get("sha256") or "").lower()
    if not warc_raw or not sha:
        return ""
    cached = warc_cache.setdefault(Path(warc_raw), {})
    if sha not in cached and Path(warc_raw).is_file():
        for rec in iter_warc_html_records(Path(warc_raw)):
            cached[str(rec.get("sha256") or "").lower()] = str(rec.get("text") or "")
    return cached.get(sha, "")


def _fetch_one(ctx: dict[str, Any], url: str, source_tag: str, *, use_crawl4ai: bool | None, stats: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch a single validated URL and record capture/dedup/blocked/error counters."""
    stats["attempted_count"] += 1
    try:
        validated = validate_heritage_url(url, allow_live_turbomopar=False)
        res = ctx["vault"].fetch_and_archive(validated, source_tag=source_tag, use_crawl4ai=use_crawl4ai, allow_live_turbomopar=False, fetch_images=ctx["fetch_images"])
    except (HijackedDomainError, AntiBotBlockedError) as exc:
        stats["blocked_count"] += 1
        ctx["blocked"].append({"source": source_tag, "url": url, "reason": str(exc), "error_type": type(exc).__name__})
        return None
    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        stats["error_count"] += 1
        ctx["errors"].append({"source": source_tag, "url": url, "error": scrub_all_pii(str(exc)), "error_type": type(exc).__name__})
        return None
    stats["deduplicated_count" if res.get("deduplicated") else "captured_count"] += 1
    stats["urls"].append(str(res.get("final_url") or validated))
    return res


def _crawl_same_domain(ctx: dict[str, Any], *, source_tag: str, domain: str, seeds: Sequence[str], frameset_priority: bool) -> dict[str, Any]:
    """Bounded same-domain crawl (FIFO BFS for minimopar, frameset/tech priority for thedodgegarage)."""
    stats, heap, seen, seq = _new_stats(), [], set(), 0

    def _push(prio: int, u: str) -> None:
        nonlocal seq
        clean = urllib.parse.urlunparse(urllib.parse.urlparse(u)._replace(fragment=""))
        if clean not in seen and not _is_non_html(clean) and _host_matches_domain(urllib.parse.urlparse(clean).hostname or "", domain):
            heapq.heappush(heap, (prio, seq, clean))
            seq += 1

    for s in seeds:
        _push(0, s)
    while heap and stats["attempted_count"] < ctx["max_pages"]:
        _, _, url = heapq.heappop(heap)
        if url in seen:
            continue
        seen.add(url)
        if (res := _fetch_one(ctx, url, source_tag, use_crawl4ai=False, stats=stats)) is None or not (html := _load_captured_html(res, ctx["warc_cache"])):
            continue
        base, links = str(res.get("final_url") or url), []
        for raw_href in _extract_hrefs(html):
            parsed = urllib.parse.urlparse(urllib.parse.urljoin(base, raw_href))
            clean = urllib.parse.urlunparse(parsed._replace(fragment=""))
            if parsed.scheme.lower() in {"http", "https"} and _validate_or_block(ctx, stats, source_tag, clean) and _host_matches_domain(parsed.hostname or "", domain) and not _is_non_html(clean):
                links.append(clean)
        if frameset_priority:
            extracted = extract_frameset_urls(html, base, url_validator=validate_heritage_url)
            for f_url in extracted.get("frame_urls", []):
                _push(0, f_url)
            for p_url in extracted.get("page_links", []):
                _push(1 if any(kw in p_url.lower() for kw in _DODGEGARAGE_TECH_KEYWORDS) else 2, p_url)
        else:
            for nxt in links:
                _push(1, nxt)
    return stats


def _safe_cdx_fetch(ctx: dict[str, Any], stats: dict[str, Any], source_tag: str, cdx_url: str) -> list[dict[str, str]]:
    try:
        if ctx["cdx_fetcher"] is None and not cdx_url.startswith("https://web.archive.org/cdx/search/cdx?"):
            raise ValueError(f"Refusing non-Wayback CDX query URL: {cdx_url!r}")
        return (ctx["cdx_fetcher"] or fetch_cdx_snapshots)(cdx_url)
    except (httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
        stats["error_count"] += 1
        ctx["errors"].append({"source": source_tag, "url": cdx_url, "error": scrub_all_pii(str(exc)), "error_type": type(exc).__name__})
        return []


def _collect_snapshots(ctx: dict[str, Any], *, sites: tuple[str, ...], source_tag: str, stats: dict[str, Any], fallback_cdx: Sequence[tuple[str, str | None, tuple[str, ...], str]]) -> list[str]:
    """Collect Wayback memento URLs from queue items and optionally cdx_fetcher."""
    urls: list[str] = []
    matched = [it for it in ctx["queue_items"] if any(s in str(it.get("source_site") or "").lower() or s in str(it.get("query_pattern") or "").lower() for s in sites)]
    can_cdx = bool(ctx["fetch_cdx"] or ctx["cdx_fetcher"] is not None)
    for item in matched:
        snaps = item.get("cdx_snapshots") or (_safe_cdx_fetch(ctx, stats, source_tag, str(item["cdx_query_url"])) if can_cdx and item.get("cdx_query_url") else [])
        for snap in snaps:
            if m_url := snap.get("memento_url"):
                urls.append(str(m_url))
            elif snap.get("timestamp") and snap.get("original"):
                try:
                    urls.append(build_memento_url(str(snap["timestamp"]), str(snap["original"]), raw=True))
                except ValueError:
                    continue
    if not urls and can_cdx:
        for pat, to_ts, f_exprs, collapse in fallback_cdx:
            urls.extend(str(s["memento_url"]) for s in _safe_cdx_fetch(ctx, stats, source_tag, build_cdx_query_url(pat, to_timestamp=to_ts, filter_exprs=f_exprs, collapse=collapse)) if s.get("memento_url"))
    return urls


def _use_c4ai_mode(ctx: dict[str, Any]) -> bool | None:
    """Route HTTPS via Crawl4AI unless an offline mock vault omitted crawl4ai_client."""
    v = ctx["vault"]
    return False if (ctx["explicit_vault"] and v.crawl4ai_client is None and v.http_client is not None) else None


def _resolve_wayback_href(raw_href: str, parent_url: str, parent_ts: str | None) -> str:
    """Resolve HTML href against Wayback memento inner URL or web.archive.org root."""
    if raw_href.startswith(("http://", "https://")):
        return raw_href
    if raw_href.startswith("/web/"):
        return f"https://web.archive.org{raw_href}"
    _, parent_inner = _unwrap_wayback_url(parent_url)
    return urllib.parse.urljoin(parent_inner if parent_ts else parent_url, raw_href)


def _fetch_cdx_mementos(ctx: dict[str, Any], source_tag: str, stats: dict[str, Any], specs: Sequence[tuple[str, str | None, tuple[str, ...], str]]) -> list[str]:
    """Run targeted CDX queries when fetch_cdx/cdx_fetcher is active."""
    if not (ctx["fetch_cdx"] or ctx["cdx_fetcher"] is not None):
        return []
    out: list[str] = []
    for pat, to_ts, f_exprs, col in specs:
        out.extend(str(s["memento_url"]) for s in _safe_cdx_fetch(ctx, stats, source_tag, build_cdx_query_url(pat, to_timestamp=to_ts, filter_exprs=f_exprs, collapse=col)) if s.get("memento_url"))
    return out


def _to_turbomopar_memento(raw_href: str, parent_url: str, parent_ts: str | None) -> str | None:
    """Resolve a discovered link to a safe pre-2017 Wayback id_ URL or mpscilink URL."""
    resolved = _resolve_wayback_href(raw_href, parent_url, parent_ts)
    ts, inner, norm = _normalize_url(resolved, strip_vb_session=True)
    host = (urllib.parse.urlparse(inner).hostname or "").lower()
    if _host_matches_domain(host, "sites.google.com") and urllib.parse.urlparse(inner).path.startswith("/site/mpscilink"):
        return norm
    if not _host_matches_domain(host, "turbo-mopar.com") or _is_non_html(inner):
        return None
    eff_ts = ts or parent_ts or f"{TURBO_MOPAR_CUTOFF_TO}235959"
    return build_memento_url(eff_ts, inner, raw=True) if eff_ts[:8] <= TURBO_MOPAR_CUTOFF_TO else None


def _tm_priority(inner: str) -> int | None:
    """Rank turbomopar/mpscilink targets; skip noisy archive/forum-chrome endpoints."""
    low = inner.lower()
    parsed = urllib.parse.urlparse(low)
    if any(skip in low for skip in ("activity.php", "member.php", "login.php", "register.php", "search.php", "newreply.php", "/system/app/pages/", "&goto=", "&mode=", "&viewfull=")):
        return None
    if parsed.path.endswith("showthread.php") and not parsed.query:
        return None
    if _host_matches_domain(parsed.hostname or "", "sites.google.com"):
        return 0 if parsed.path.rstrip("/") in {"/site/mpscilink", "/site/mpscilink/mpflash", "/site/mpscilink/operational-info-how-to"} else 2
    if "/t-" in low or ("showthread.php" in low and parsed.query):
        return 0 if any(k in low for k in _DODGEGARAGE_TECH_KEYWORDS + ("smec", "sbec", "28c256", "chemasm", "d-cal", "calibration", "wiring", "logic")) else 1
    if "/forums/archive/" in parsed.path:
        return 0 if parsed.path.endswith(("index.php", "/")) or "/f-" in parsed.path else 1
    return 3


def _crawl_turbomopar(ctx: dict[str, Any]) -> dict[str, Any]:
    """Crawl mpscilink and pre-Cloudflare Wayback turbo-mopar.com/forums/ mementos."""
    stats, heap, seen_inner, seq = _new_stats(), [], set(), 0
    fallback = (("turbo-mopar.com/forums/archive/*", TURBO_MOPAR_CUTOFF_TO, (), "urlkey"), ("turbo-mopar.com/forums/showthread.php*", TURBO_MOPAR_CUTOFF_TO, (), "urlkey"))
    cands = [TURBOMOPAR_SEED] + _collect_snapshots(ctx, sites=("turbo-mopar.com", "mpscilink"), source_tag="turbomopar", stats=stats, fallback_cdx=fallback) + _fetch_cdx_mementos(ctx, "turbomopar", stats, fallback)

    def _enqueue(cand: str) -> None:
        nonlocal seq
        if not _validate_or_block(ctx, stats, "turbomopar", cand):
            return
        ts, inner, norm = _normalize_url(cand, strip_vb_session=True)
        if _host_matches_domain(urllib.parse.urlparse(inner).hostname or "", "turbo-mopar.com") and (ts is None or ts[:8] > TURBO_MOPAR_CUTOFF_TO):
            return
        canon = inner.lower().rstrip("/")
        if canon not in seen_inner and not _is_non_html(inner) and (prio := _tm_priority(inner)) is not None:
            seen_inner.add(canon)
            heapq.heappush(heap, (prio, seq, norm))
            seq += 1

    for c in cands:
        _enqueue(c)
    while heap and stats["attempted_count"] < ctx["max_pages"]:
        _, _, target = heapq.heappop(heap)
        if (res := _fetch_one(ctx, target, "turbomopar", use_crawl4ai=_use_c4ai_mode(ctx), stats=stats)) is None or not (html := _load_captured_html(res, ctx["warc_cache"])):
            continue
        parent_ts, _ = _unwrap_wayback_url(target)
        for href in _extract_hrefs(html):
            if _validate_or_block(ctx, stats, "turbomopar", _resolve_wayback_href(href, target, parent_ts), allow_live_turbomopar=True) and (nxt := _to_turbomopar_memento(href, target, parent_ts)):
                p = urllib.parse.urlparse(_normalize_url(nxt, strip_vb_session=True)[1])
                if _TM_ARCHIVE_RE.search(p.path) or _host_matches_domain(p.hostname or "", "sites.google.com"):
                    _enqueue(nxt)
    return stats


def _is_valid_allpar_memento(memento_url: str) -> tuple[bool, str]:
    """Validate Allpar Wayback timestamp <= 20170531, non-forum path, and 7 prefixes."""
    ts, inner, norm = _normalize_url(memento_url, strip_vb_session=False)
    path_low = urllib.parse.urlparse(inner).path.lower()
    if ts is None or ts[:8] > ALLPAR_CUTOFF_TO or "/forums/" in path_low or path_low.endswith("/forums"):
        return False, ""
    try:
        validate_wave_e_query_pattern(inner, source_kind="wayback_cdx")
        validate_heritage_url(norm, allow_live_turbomopar=False)
    except (ValueError, RuntimeError):
        return False, ""
    return not _is_non_html(inner), norm


def _crawl_allpar(ctx: dict[str, Any]) -> dict[str, Any]:
    """Crawl pre-sale Allpar Wayback id_ mementos matching the 7 prefixes and cutoff."""
    stats, heap, seen_inner, seq = _new_stats(), [], set(), 0
    fallback = tuple((p, ALLPAR_CUTOFF_TO, ("!original:.*/forums/.*",), "digest") for p in ALLPAR_PREFIXES)
    targeted = (
        ("allpar.com/mopar/22*", ALLPAR_CUTOFF_TO, ("!original:.*/forums/.*",), "urlkey"),
        ("allpar.com/model/*", ALLPAR_CUTOFF_TO, ("!original:.*/forums/.*",), "urlkey"),
        ("allpar.com/cars/dodge/*", ALLPAR_CUTOFF_TO, ("!original:.*/forums/.*",), "urlkey"),
        ("allpar.com/fix/*", ALLPAR_CUTOFF_TO, ("!original:.*/forums/.*",), "urlkey"),
    )
    raw_cands = _collect_snapshots(ctx, sites=("allpar.com",), source_tag="allpar", stats=stats, fallback_cdx=fallback) + _fetch_cdx_mementos(ctx, "allpar", stats, targeted)

    def _push(raw_url: str, *, is_cdx: bool) -> None:
        nonlocal seq
        if not _validate_or_block(ctx, stats, "allpar", raw_url):
            return
        ok, norm = _is_valid_allpar_memento(raw_url)
        if not ok:
            return
        _, inner = _unwrap_wayback_url(norm)
        canon = re.sub(r"/index\.html?$", "/", inner.lower()).rstrip("/") or inner.lower()
        if canon not in seen_inner:
            seen_inner.add(canon)
            hits = sum(1 for kw in _ALLPAR_TECH_KEYWORDS if kw in inner.lower())
            heapq.heappush(heap, ((0 if is_cdx else 10) - hits, seq, norm))
            seq += 1

    for raw in raw_cands:
        _push(raw, is_cdx=True)
    while heap and stats["attempted_count"] < ctx["max_pages"]:
        _, _, target = heapq.heappop(heap)
        if (res := _fetch_one(ctx, target, "allpar", use_crawl4ai=_use_c4ai_mode(ctx), stats=stats)) is None or not (html := _load_captured_html(res, ctx["warc_cache"])):
            continue
        parent_ts, _ = _unwrap_wayback_url(target)
        for href in _extract_hrefs(html):
            resolved = _resolve_wayback_href(href, target, parent_ts)
            _, r_inner = _unwrap_wayback_url(resolved)
            if not resolved.startswith(("http://", "https://")) or not _host_matches_domain(urllib.parse.urlparse(r_inner).hostname or "", "allpar.com"):
                continue
            try:
                _push(resolved if "/web/" in urllib.parse.urlparse(resolved).path else build_memento_url(parent_ts or ALLPAR_CUTOFF_TO, resolved, raw=True), is_cdx=False)
            except ValueError:
                continue
    return stats


def run_wave_e_crawl(
    *, sources: Sequence[str] | None = None, max_pages_per_source: int = 35,
    queue_path: Path | str = DEFAULT_QUEUE_PATH, warc_root: Path | str = DEFAULT_WARC_ROOT,
    figures_root: Path | str = DEFAULT_FIGURES_ROOT, manifest_path: Path | str | None = DEFAULT_MANIFEST_PATH,
    fetch_images: bool = True, fetch_cdx: bool = False, delay_s: float = 1.2,
    vault: HeritageWarcVault | None = None, cdx_fetcher: Callable[[str], list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Execute bounded polite Wave E crawl across the 4 canonical heritage sources."""
    if max_pages_per_source < 1:
        raise ValueError(f"max_pages_per_source must be >= 1, got {max_pages_per_source}")
    qpath, wroot = _reject_symlink(queue_path, label="queue_path"), _reject_symlink(warc_root, label="warc_root")
    froot, mpath = _reject_symlink(figures_root, label="figures_root"), _reject_symlink(manifest_path, label="manifest_path")
    chosen = list(WAVE_E_SOURCES) if not sources else list(dict.fromkeys(s.strip().lower() for s in sources if s.strip()))
    if unknown := [s for s in chosen if s not in WAVE_E_SOURCES]:
        raise ValueError(f"Unsupported Wave E source(s): {unknown}. Allowed: {list(WAVE_E_SOURCES)}")

    active_vault = vault or HeritageWarcVault(warc_root=wroot, figures_root=froot, manifest_path=mpath, min_delay_s=delay_s)
    try:
        raw_q = json.loads(qpath.read_text(encoding="utf-8")) if qpath is not None and qpath.is_file() else {}
        q_list = raw_q.get("queue", []) if isinstance(raw_q, dict) else (raw_q if isinstance(raw_q, list) else [])
        ctx: dict[str, Any] = {
            "vault": active_vault, "explicit_vault": vault is not None, "max_pages": int(max_pages_per_source),
            "fetch_images": bool(fetch_images), "fetch_cdx": bool(fetch_cdx), "cdx_fetcher": cdx_fetcher,
            "warc_cache": {}, "queue_items": [dict(x) for x in q_list if isinstance(x, dict)], "blocked": [], "errors": [],
        }
        runners: dict[str, Callable[[], dict[str, Any]]] = {
            "minimopar": lambda: _crawl_same_domain(ctx, source_tag="minimopar", domain="minimopar.net", seeds=MINIMOPAR_SEEDS, frameset_priority=False),
            "thedodgegarage": lambda: _crawl_same_domain(ctx, source_tag="thedodgegarage", domain="thedodgegarage.com", seeds=DODGEGARAGE_SEEDS, frameset_priority=True),
            "turbomopar": lambda: _crawl_turbomopar(ctx),
            "allpar": lambda: _crawl_allpar(ctx),
        }
        per_source = {src: runners[src]() for src in chosen}
        return {
            "sources": chosen, "captured_count": sum(v["captured_count"] for v in per_source.values()),
            "deduplicated_count": sum(v["deduplicated_count"] for v in per_source.values()),
            "blocked_count": len(ctx["blocked"]), "error_count": len(ctx["errors"]), "per_source": per_source,
            "blocked": ctx["blocked"], "errors": ctx["errors"],
            "warc_root": str(active_vault.warc_root), "figures_root": str(active_vault.figures_root),
        }
    finally:
        if vault is None:
            active_vault.close()
