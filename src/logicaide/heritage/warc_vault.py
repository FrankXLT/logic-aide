"""ISO 28500 WARC archival vault with Option B Crawl4AI/httpx routing and SHA-256 dedup."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from http import HTTPStatus
import io
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence
import urllib.parse

import httpx
from warcio.archiveiterator import ArchiveIterator
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from logicaide.heritage.bom_extractor import check_quarantine
from logicaide.heritage.crawl4ai_client import (
    AntiBotBlockedError, Crawl4AIClient, HijackedDomainError, detect_antibot_block, validate_heritage_url,
)
from logicaide.heritage.discovery import DEFAULT_USER_AGENT, collect_vault_sha256

FIGURE_EXTENSIONS = frozenset({".gif", ".jpg", ".jpeg", ".png"})
_HTML_MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_STRIP_HTTP_HEADERS = frozenset({"transfer-encoding", "content-encoding", "content-length", "connection"})
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_CHARSET_RE = re.compile(r"charset=([A-Za-z0-9._-]+)", re.IGNORECASE)


class _ImageLinkParser(HTMLParser):
    """Collect inline image and schematic link references from HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "").strip() for k, v in attrs}
        t = tag.lower()
        if t == "img":
            self.refs.extend(val for key in ("src", "data-src") if (val := attr_map.get(key)))
        elif t == "a" and (href := attr_map.get("href")):
            if Path(urllib.parse.urlparse(href).path).suffix.lower() in FIGURE_EXTENSIONS:
                self.refs.append(href)


def _validate_source_tag(source_tag: str) -> str:
    """Validate source_tag as a safe single-segment directory name."""
    tag = str(source_tag).strip()
    if not tag or "/" in tag or "\\" in tag or tag in {".", ".."} or ".." in tag:
        raise ValueError(f"Invalid source_tag: {source_tag!r}")
    return tag


def _normalize_headers(
    headers: Mapping[str, str] | Sequence[tuple[str, str]] | None, *, content_type: str, body_len: int
) -> list[tuple[str, str]]:
    """Build sanitized HTTP header tuples for WARC StatusAndHeaders."""
    pairs = list(headers.items()) if isinstance(headers, Mapping) else list(headers or ())
    cleaned = [(str(k).strip(), str(v)) for k, v in pairs if str(k).strip() and str(k).strip().lower() not in _STRIP_HTTP_HEADERS]
    if content_type and not any(k.lower() == "content-type" for k, _ in cleaned):
        cleaned.append(("Content-Type", content_type))
    cleaned.append(("Content-Length", str(body_len)))
    return cleaned


def _extract_image_urls(html: str, base_url: str) -> list[str]:
    """Resolve unique inline image/schematic URLs (.gif/.jpg/.jpeg/.png) from HTML."""
    parser = _ImageLinkParser()
    parser.feed(html)
    seen, urls = set(), []
    for ref in parser.refs:
        if not ref or ref.startswith(("data:", "javascript:", "mailto:")):
            continue
        resolved = urllib.parse.urljoin(base_url, ref)
        parsed = urllib.parse.urlparse(resolved)
        if parsed.scheme.lower() in {"http", "https"} and Path(parsed.path).suffix.lower() in FIGURE_EXTENSIONS and resolved not in seen:
            seen.add(resolved)
            urls.append(resolved)
    return urls


def write_warc_record(
    warc_path: Path | str, url: str, status_code: int,
    headers: Mapping[str, str] | Sequence[tuple[str, str]] | None,
    body_bytes: bytes, method: str = "GET", content_type: str = "text/html",
) -> str:
    """Append an ISO 28500 HTTP response record to warc_path and return payload SHA-256."""
    _ = method
    path = Path(warc_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sha256 = hashlib.sha256(body_bytes).hexdigest().lower()
    code = int(status_code)
    try:
        phrase = HTTPStatus(code).phrase
    except ValueError:
        phrase = "OK"
    http_headers = StatusAndHeaders(
        f"{code} {phrase}", _normalize_headers(headers, content_type=content_type, body_len=len(body_bytes)), protocol="HTTP/1.1"
    )
    gzip_enabled = path.suffix.lower() == ".gz" or path.name.lower().endswith(".warc.gz")
    with path.open("ab") as fh:
        writer = WARCWriter(fh, gzip=gzip_enabled)
        writer.write_record(writer.create_warc_record(
            str(url), "response", payload=io.BytesIO(body_bytes),
            warc_headers_dict={"WARC-Payload-Digest": f"sha256:{sha256}"}, http_headers=http_headers,
        ))
    return sha256


def iter_warc_html_records(warc_path: Path | str) -> list[dict[str, Any]]:
    """Read HTML response records from a .warc or .warc.gz archive."""
    records: list[dict[str, Any]] = []
    with Path(warc_path).open("rb") as fh:
        for rec in ArchiveIterator(fh):
            if rec.rec_type != "response":
                continue
            ct = rec.http_headers.get_header("Content-Type") if rec.http_headers else ""
            if (ct or "").split(";", 1)[0].strip().lower() not in _HTML_MIME_TYPES:
                continue
            body_bytes = rec.content_stream().read()
            m = _CHARSET_RE.search(ct or "")
            try:
                text = body_bytes.decode(m.group(1) if m else "utf-8", errors="replace")
            except LookupError:
                text = body_bytes.decode("utf-8", errors="replace")
            status_raw = rec.http_headers.get_statuscode() if rec.http_headers else None
            records.append({
                "url": rec.rec_headers.get_header("WARC-Target-URI") or "",
                "warc_date": rec.rec_headers.get_header("WARC-Date") or "",
                "status_code": int(status_raw) if status_raw and str(status_raw).isdigit() else 200,
                "content_type": ct or "text/html",
                "sha256": hashlib.sha256(body_bytes).hexdigest().lower(),
                "body_bytes": body_bytes,
                "text": text,
            })
    return records


def archive_figure(
    image_bytes: bytes, dest_dir: Path | str, filename: str,
    *, original_url: str | None = None, existing_hashes: set[str] | None = None,
) -> dict[str, Any]:
    """Save a figure image (.gif/.jpg/.jpeg/.png) with SHA-256 deduplication."""
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _SAFE_NAME_RE.sub("_", Path(urllib.parse.unquote(filename)).name).lstrip(".") or "figure.png"
    ext = Path(safe_name).suffix.lower()
    if ext not in FIGURE_EXTENSIONS:
        raise ValueError(f"Unsupported figure extension {ext!r} for {filename!r}")

    sha256 = hashlib.sha256(image_bytes).hexdigest().lower()
    if existing_hashes is not None and sha256 in existing_hashes:
        return {"path": None, "filename": safe_name, "sha256": sha256, "bytes": len(image_bytes), "original_url": original_url, "deduplicated": True}

    target = out_dir / safe_name
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest().lower() == sha256:
            if existing_hashes is not None:
                existing_hashes.add(sha256)
            return {"path": str(target), "filename": target.name, "sha256": sha256, "bytes": len(image_bytes), "original_url": original_url, "deduplicated": True}
        target = out_dir / f"{target.stem}_{sha256[:8]}{ext}"

    target.write_bytes(image_bytes)
    if existing_hashes is not None:
        existing_hashes.add(sha256)
    return {"path": str(target), "filename": target.name, "sha256": sha256, "bytes": len(image_bytes), "original_url": original_url, "deduplicated": False}


class HeritageWarcVault:
    """Polite ISO 28500 WARC vault with Option B Crawl4AI/httpx routing and dedup."""

    def __init__(
        self, warc_root: Path | str, figures_root: Path | str, manifest_path: Path | str | None = None,
        crawl4ai_client: Crawl4AIClient | None = None, http_client: httpx.Client | None = None, min_delay_s: float = 1.2,
    ) -> None:
        self.warc_root, self.figures_root = Path(warc_root), Path(figures_root)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self._owns_crawl4ai_client = crawl4ai_client is None
        self._owns_http_client = http_client is None
        self.crawl4ai_client, self.http_client = crawl4ai_client, http_client
        self.min_delay_s = float(min_delay_s)
        self._last_host_ts: dict[str, float] = {}
        self._vault_hashes = self._load_initial_hashes()

    def __enter__(self) -> HeritageWarcVault:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close any internally created clients and reset their references."""
        if self._owns_crawl4ai_client and self.crawl4ai_client is not None:
            self.crawl4ai_client.close()
            self.crawl4ai_client = None
        if self._owns_http_client and self.http_client is not None:
            self.http_client.close()
            self.http_client = None

    def _load_initial_hashes(self) -> set[str]:
        """Collect SHA-256 hashes from manifest, files on disk, and capture_index JSONLs."""
        hashes = collect_vault_sha256(self.manifest_path, self.warc_root)
        if self.warc_root.is_dir():
            for idx_path in self.warc_root.rglob("capture_index.jsonl"):
                for line in idx_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.strip():
                        try:
                            if sha := json.loads(line).get("sha256"):
                                hashes.add(str(sha).lower())
                        except json.JSONDecodeError:
                            continue
        return hashes

    def _throttle(self, url: str) -> None:
        """Enforce polite per-host request spacing."""
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if not host or self.min_delay_s <= 0:
            return
        now = time.monotonic()
        if (last := self._last_host_ts.get(host)) is not None and (wait := self.min_delay_s - (now - last)) > 0:
            time.sleep(wait)
        self._last_host_ts[host] = time.monotonic()

    def _get_http_client(self) -> httpx.Client:
        if self.http_client is None:
            self.http_client = httpx.Client(follow_redirects=False, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=30.0)
        return self.http_client

    def _safe_get(
        self, url: str, *, allow_live_turbomopar: bool, max_redirects: int = 5
    ) -> tuple[httpx.Response, str]:
        """Execute GET with per-hop URL validation so hijacked redirects are never fetched."""
        client = self._get_http_client()
        current = validate_heritage_url(url, allow_live_turbomopar=allow_live_turbomopar)
        for _ in range(max_redirects + 1):
            self._throttle(current)
            resp = client.get(current, follow_redirects=False)
            location = str(resp.headers.get("location") or "")
            if reason := detect_antibot_block(resp.status_code, resp.text or "", location):
                raise AntiBotBlockedError(reason)
            if resp.status_code in {301, 302, 303, 307, 308} and location:
                current = validate_heritage_url(
                    urllib.parse.urljoin(current, location), allow_live_turbomopar=allow_live_turbomopar
                )
                continue
            final_url = validate_heritage_url(str(resp.url or current), allow_live_turbomopar=allow_live_turbomopar)
            return resp, final_url
        raise ValueError(f"Exceeded {max_redirects} redirects while fetching {url}")

    def _fetch_direct_httpx(self, url: str, *, allow_live_turbomopar: bool) -> tuple[str, int, dict[str, str], bytes, str]:
        """Fetch URL directly via polite httpx with redirect hijack and anti-bot checks."""
        resp, final_url = self._safe_get(url, allow_live_turbomopar=allow_live_turbomopar)
        resp.raise_for_status()
        headers = dict(resp.headers.items())
        return final_url, resp.status_code, headers, bytes(resp.content), headers.get("content-type") or headers.get("Content-Type") or "text/html"

    def _fetch_via_option_b(
        self, url: str, *, use_crawl4ai: bool | None, allow_live_turbomopar: bool
    ) -> tuple[str, int, dict[str, str], bytes, str, str]:
        """Route https:// through Crawl4AI and plain http:// through polite httpx."""
        route_crawl4ai = (urllib.parse.urlparse(url).scheme.lower() == "https") if use_crawl4ai is None else bool(use_crawl4ai)
        if not route_crawl4ai:
            return (*self._fetch_direct_httpx(url, allow_live_turbomopar=allow_live_turbomopar), "httpx")

        self.crawl4ai_client = self.crawl4ai_client or Crawl4AIClient()
        self._throttle(url)
        try:
            res = self.crawl4ai_client.crawl(url, allow_live_turbomopar=allow_live_turbomopar)
        except (AntiBotBlockedError, HijackedDomainError):
            raise
        except Exception as exc:
            if "ERR_BLOCKED_BY_CLIENT" in str(exc) and not detect_antibot_block(None, "", str(exc)):
                return (*self._fetch_direct_httpx(url, allow_live_turbomopar=allow_live_turbomopar), "crawl4ai_fallback_httpx")
            raise

        err_msg, html = str(res.get("error_message") or ""), str(res.get("html") or "")
        if "ERR_BLOCKED_BY_CLIENT" in err_msg and not html.strip():
            if reason := detect_antibot_block(res.get("status_code"), html, err_msg):
                raise AntiBotBlockedError(reason)
            return (*self._fetch_direct_httpx(url, allow_live_turbomopar=allow_live_turbomopar), "crawl4ai_fallback_httpx")

        final_url = validate_heritage_url(
            str(res.get("redirected_url") or res.get("url") or url), allow_live_turbomopar=allow_live_turbomopar
        )
        raw_headers = res.get("response_headers") or res.get("headers") or {}
        headers = {str(k): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, Mapping) else {}
        ct = headers.get("content-type") or headers.get("Content-Type") or "text/html; charset=utf-8"
        return final_url, int(res.get("status_code") or 200), headers, html.encode("utf-8"), ct, "crawl4ai"

    def _archive_inline_figures(
        self, html: str, base_url: str, source_tag: str, *, allow_live_turbomopar: bool
    ) -> list[dict[str, Any]]:
        """Download and deduplicate inline .gif/.jpg/.jpeg/.png figures from archived HTML."""
        dest_dir, archived = self.figures_root / source_tag, []
        base_host = (urllib.parse.urlparse(base_url).hostname or "").lower().rstrip(".")
        base_apex = base_host.removeprefix("www.")
        for img_url in _extract_image_urls(html, base_url):
            img_host = (urllib.parse.urlparse(img_url).hostname or "").lower().rstrip(".")
            if not (img_host == "web.archive.org" or (bool(base_apex) and (img_host == base_apex or img_host.endswith(f".{base_apex}")))):
                continue
            try:
                resp, safe_img = self._safe_get(img_url, allow_live_turbomopar=allow_live_turbomopar)
                resp.raise_for_status()
                ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if ct in _HTML_MIME_TYPES:
                    continue
                fname = Path(urllib.parse.urlparse(safe_img).path).name or "figure.png"
                archived.append(
                    archive_figure(bytes(resp.content), dest_dir, fname, original_url=safe_img, existing_hashes=self._vault_hashes)
                )
            except (AntiBotBlockedError, HijackedDomainError, httpx.HTTPError, ValueError):
                continue
        return archived

    def _record_capture(
        self, tag: str, *, url: str, final_url: str, status: int, content_type: str,
        sha256: str, warc_path: Path, transport: str, figures: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Append provenance JSONL record under warc_root / tag / capture_index.jsonl."""
        entry = {
            "archived_at": datetime.now(timezone.utc).isoformat(), "source_tag": tag, "url": url,
            "final_url": final_url, "status_code": status, "content_type": content_type,
            "sha256": sha256, "warc_path": str(warc_path), "transport": transport,
            "figures": [f for f in figures if f.get("path")],
        }
        index_path = self.warc_root / tag / "capture_index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def fetch_and_archive(
        self, url: str, source_tag: str, *, use_crawl4ai: bool | None = None,
        allow_live_turbomopar: bool = False, fetch_images: bool = True,
    ) -> dict[str, Any]:
        """Fetch a validated URL, deduplicate by SHA-256, write WARC + figures + JSONL index."""
        tag = _validate_source_tag(source_tag)
        validated = validate_heritage_url(url, allow_live_turbomopar=allow_live_turbomopar)
        final_url, status, headers, body_bytes, ct, transport = self._fetch_via_option_b(
            validated, use_crawl4ai=use_crawl4ai, allow_live_turbomopar=allow_live_turbomopar
        )
        sha256 = hashlib.sha256(body_bytes).hexdigest().lower()
        warc_path = self.warc_root / tag / f"{tag}.warc.gz"
        if sha256 in self._vault_hashes:
            return {
                "status": "deduplicated", "deduplicated": True, "source_tag": tag, "url": validated,
                "final_url": final_url, "sha256": sha256, "warc_path": str(warc_path), "transport": transport, "figures": [],
            }
        write_warc_record(warc_path, final_url, status, headers, body_bytes, content_type=ct)
        self._vault_hashes.add(sha256)
        figures = self._archive_inline_figures(
            body_bytes.decode("utf-8", errors="replace"), final_url, tag, allow_live_turbomopar=allow_live_turbomopar
        ) if fetch_images and "html" in ct.lower() else []
        entry = self._record_capture(
            tag, url=validated, final_url=final_url, status=status, content_type=ct,
            sha256=sha256, warc_path=warc_path, transport=transport, figures=figures,
        )
        return {"status": "archived", "deduplicated": False, **entry}

    def ingest_local_archive(
        self, source_path: Path | str, source_tag: str, original_url: str | None = None
    ) -> dict[str, Any]:
        """Ingest a local .html/.htm or .warc/.warc.gz file after quarantine check."""
        raw_path = Path(source_path)
        if raw_path.is_symlink():
            raise ValueError(f"Refusing symlink local archive: {raw_path}")
        checked_path = check_quarantine(source_path)
        check_quarantine(checked_path.resolve())
        tag = _validate_source_tag(source_tag)
        if original_url is not None:
            validate_heritage_url(original_url, allow_local_archive=True)
        if not checked_path.is_file():
            raise FileNotFoundError(f"Local archive not found: {checked_path}")

        warc_path = self.warc_root / tag / f"{tag}.warc.gz"
        if checked_path.name.lower().endswith((".warc", ".warc.gz")):
            raw_records = iter_warc_html_records(checked_path)
        elif checked_path.suffix.lower() in {".html", ".htm"}:
            raw_bytes = checked_path.read_bytes()
            raw_records = [{
                "url": original_url or f"http://local-archive.invalid/{tag}/{checked_path.name}",
                "status_code": 200, "content_type": "text/html; charset=utf-8",
                "sha256": hashlib.sha256(raw_bytes).hexdigest().lower(), "body_bytes": raw_bytes,
            }]
        else:
            raise ValueError(f"Unsupported local archive format: {checked_path.name}")

        ingested, deduplicated = 0, 0
        for rec in raw_records:
            rec_url = validate_heritage_url(str(rec["url"]), allow_local_archive=True)
            sha = str(rec["sha256"]).lower()
            if sha in self._vault_hashes:
                deduplicated += 1
                continue
            write_warc_record(warc_path, rec_url, int(rec["status_code"]), {}, rec["body_bytes"], content_type=rec["content_type"])
            self._vault_hashes.add(sha)
            self._record_capture(
                tag, url=rec_url, final_url=rec_url, status=int(rec["status_code"]),
                content_type=rec["content_type"], sha256=sha, warc_path=warc_path, transport="local_ingest", figures=[],
            )
            ingested += 1
        return {
            "status": "ingested" if ingested else "deduplicated", "source_tag": tag,
            "warc_path": str(warc_path), "ingested_count": ingested, "deduplicated_count": deduplicated,
        }
