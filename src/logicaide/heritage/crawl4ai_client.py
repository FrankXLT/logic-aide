"""Crawl4AI gateway client with cleanroom hijack & anti-bot guardrails."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import stat
from typing import Any
import urllib.parse
import warnings

import httpx

DEFAULT_CRAWL4AI_URL = "http://192.168.3.173:11235"
DEFAULT_SECRETS_PATH = Path.home() / ".config" / "mcp-crawl4ai" / "secrets.env"
HIJACKED_DOMAINS: tuple[str, ...] = ("turbo-mopar.net", "homemadeturbo.com")
_TOKEN_ENV_KEY = "CRAWL4AI_API_TOKEN"
_WAYBACK_PATH_RE = re.compile(r"^/web/(\d+(?:[a-z_]+)?|\*)/(.+)$", re.IGNORECASE)
_CF_CHALLENGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("cf-chl", "Cloudflare JS challenge marker (cf-chl)"),
    ("_cf_chl_opt", "Cloudflare JS challenge options"),
    ("cdn-cgi/challenge-platform", "Cloudflare challenge-platform"),
    ("challenges.cloudflare.com/turnstile", "Cloudflare Turnstile widget"),
    ("cf-turnstile", "Cloudflare Turnstile challenge"),
    ("blocked by anti-bot protection", "Blocked by anti-bot protection"),
    ("verify you are human", "Human verification / CAPTCHA gate"),
    ("h-captcha", "hCaptcha challenge"),
    ("g-recaptcha", "reCAPTCHA challenge"),
    ("recaptcha/api.js", "reCAPTCHA script"),
    ("altcha", "Altcha proof-of-work challenge"),
)


class HijackedDomainError(ValueError):
    """Raised when a target URL or Wayback inner URL points to a hijacked domain."""


class AntiBotBlockedError(RuntimeError):
    """Raised immediately when an anti-bot, Turnstile, or CAPTCHA gate is detected."""


def _host_matches_domain(host: str, domain: str) -> bool:
    """Return True if host is an exact or subdomain match for domain."""
    clean_host = host.strip().lower().rstrip(".")
    clean_domain = domain.strip().lower().rstrip(".")
    return clean_host == clean_domain or clean_host.endswith(f".{clean_domain}")


def _extract_wayback_inner_url(parsed: urllib.parse.ParseResult) -> str | None:
    """Extract the inner archived URL from a web.archive.org/web/<ts>.../<url> path."""
    if not _host_matches_domain(parsed.hostname or "", "web.archive.org"):
        return None
    match = _WAYBACK_PATH_RE.match(parsed.path or "")
    if not match:
        return None
    inner = match.group(2)
    if parsed.query:
        inner = f"{inner}?{parsed.query}"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", inner):
        inner = f"http://{inner.lstrip('/')}"
    return inner


def _parse_env_token(text: str) -> str:
    """Parse CRAWL4AI_API_TOKEN from dotenv-formatted text without exposing values."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() != _TOKEN_ENV_KEY:
            continue
        cleaned = val.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1]
        if cleaned:
            return cleaned
    return ""


def load_crawl4ai_token(secrets_path: Path | str | None = None) -> str:
    """Load Crawl4AI bearer token from env or secrets file without leaking it."""
    env_token = os.environ.get(_TOKEN_ENV_KEY, "").strip()
    if env_token:
        return env_token
    path = Path(secrets_path) if secrets_path is not None else DEFAULT_SECRETS_PATH
    if path.is_symlink():
        raise ValueError(f"Refusing symlink Crawl4AI secrets file: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"Crawl4AI secrets file not found: {path}")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        warnings.warn(
            f"Crawl4AI secrets file {path} permissions {oct(mode)} are broader than 0600",
            UserWarning,
            stacklevel=2,
        )
    token = _parse_env_token(path.read_text(encoding="utf-8", errors="ignore"))
    if not token:
        raise ValueError(f"{_TOKEN_ENV_KEY} is missing or empty in {path}")
    return token


def _assert_public_host(
    parsed: urllib.parse.ParseResult, url: str, *, allow_local_archive: bool = False
) -> str:
    """Reject credentials, localhost/internal suffixes, integer/hex hosts, and non-public IPs."""
    if parsed.username or parsed.password:
        raise ValueError(f"URL must not contain credentials: {url!r}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if allow_local_archive and host == "local-archive.invalid":
        return host
    if (
        not host
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith((".localhost", ".local", ".internal", ".invalid"))
        or host.isdigit()
        or host.startswith("0x")
    ):
        raise ValueError(f"Refusing non-public heritage host {host!r} in {url!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        raise ValueError(f"Refusing non-public IP address {host!r} in {url!r}")
    return host


def validate_heritage_url(
    url: str, *, allow_live_turbomopar: bool = False, allow_local_archive: bool = False
) -> str:
    """Validate HTTP(S) scheme and enforce public-host, hijack, and Cloudflare policies."""
    raw = str(url).strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid heritage URL (http/https with host required): {url!r}")
    host = _assert_public_host(parsed, url, allow_local_archive=allow_local_archive)

    for domain in HIJACKED_DOMAINS:
        if _host_matches_domain(host, domain):
            raise HijackedDomainError(f"Refusing hijacked domain {host!r} (matched {domain})")

    inner_url = _extract_wayback_inner_url(parsed)
    if inner_url is not None:
        inner_parsed = urllib.parse.urlparse(inner_url)
        if inner_parsed.scheme.lower() not in {"http", "https"} or not inner_parsed.hostname:
            raise ValueError(f"Invalid Wayback inner URL in {url!r}")
        inner_host = _assert_public_host(inner_parsed, inner_url, allow_local_archive=False)
        for domain in HIJACKED_DOMAINS:
            if _host_matches_domain(inner_host, domain):
                raise HijackedDomainError(
                    f"Refusing hijacked Wayback inner domain {inner_host!r} (matched {domain})"
                )
        return raw

    if _host_matches_domain(host, "turbo-mopar.com") and not allow_live_turbomopar:
        raise AntiBotBlockedError(
            "Live turbo-mopar.com is Cloudflare-gated; use pre-2017 Wayback mementos or local archives"
        )
    return raw


def detect_antibot_block(
    status_code: int | None, html: str = "", error_message: str = ""
) -> str | None:
    """Detect Cloudflare JS/Turnstile/CAPTCHA or 307/403 challenge blocks."""
    combined = f"{error_message}\n{html}".lower()
    for marker, reason in _CF_CHALLENGE_MARKERS:
        if marker in combined:
            return reason

    if "just a moment" in combined and ("cloudflare" in combined or "ray id" in combined):
        return "Cloudflare 'Just a moment' JS challenge"
    if "checking your browser" in combined and "cloudflare" in combined:
        return "Cloudflare browser check challenge"
    if "attention required" in combined and "cloudflare" in combined:
        return "Cloudflare attention required / CAPTCHA gate"

    if status_code in {307, 403}:
        if any(tok in combined for tok in ("cloudflare", "challenge", "captcha", "anti-bot", "bot protection", "cf-ray")):
            return f"HTTP {status_code} anti-bot challenge page"
        if status_code == 307 and not html.strip():
            return "HTTP 307 empty challenge redirect"
    return None


class Crawl4AIClient:
    """HTTP gateway client for Crawl4AI with hijack and anti-bot enforcement."""

    def __init__(
        self,
        base_url: str = DEFAULT_CRAWL4AI_URL,
        api_token: str | None = None,
        secrets_path: Path | str | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._secrets_path = secrets_path
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self.default_timeout = float(timeout)

    def __repr__(self) -> str:
        return f"Crawl4AIClient(base_url={self.base_url!r}, timeout={self.default_timeout!r})"

    def __enter__(self) -> Crawl4AIClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying httpx.Client if owned by this instance."""
        if self._owns_client:
            self._client.close()

    def _resolve_token(self) -> str | None:
        """Resolve bearer token; allow unconfigured token only for injected test clients."""
        if self._api_token:
            return self._api_token
        if self._secrets_path is not None or self._owns_client:
            return load_crawl4ai_token(self._secrets_path)
        try:
            return load_crawl4ai_token(None)
        except (FileNotFoundError, ValueError):
            return None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token := self._resolve_token():
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request_json(
        self, method: str, endpoint: str, *, payload: dict[str, Any] | None = None, timeout: float | None = None
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{endpoint}"
        resp = self._client.request(
            method,
            url,
            json=payload,
            headers=self._headers(),
            timeout=timeout or self.default_timeout,
        )
        body_text = resp.text if hasattr(resp, "text") else ""
        if reason := detect_antibot_block(resp.status_code, body_text):
            raise AntiBotBlockedError(reason)
        resp.raise_for_status()
        data = resp.json()
        return resp.status_code, data if isinstance(data, dict) else {"result": data}

    def health(self) -> dict[str, Any]:
        """Query Crawl4AI gateway /health endpoint."""
        _, data = self._request_json("GET", "/health", timeout=min(self.default_timeout, 15.0))
        return data

    def crawl(
        self,
        url: str,
        timeout: float = 45.0,
        *,
        allow_live_turbomopar: bool = False,
    ) -> dict[str, Any]:
        """Crawl a validated heritage URL via Crawl4AI /crawl without retrying blocks."""
        validated = validate_heritage_url(url, allow_live_turbomopar=allow_live_turbomopar)
        http_status, data = self._request_json(
            "POST", "/crawl", payload={"urls": [validated]}, timeout=timeout
        )
        results = data.get("results")
        item: dict[str, Any] = (
            results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else data
        )
        final_url = str(item.get("redirected_url") or item.get("url") or validated)
        validate_heritage_url(final_url, allow_live_turbomopar=allow_live_turbomopar)

        html = str(item.get("html") or item.get("cleaned_html") or "")
        err_msg = str(item.get("error_message") or item.get("error") or "")
        page_status = item.get("status_code")
        if isinstance(page_status, bool):
            page_status = None
        if reason := detect_antibot_block(
            int(page_status) if isinstance(page_status, int) else http_status,
            html,
            err_msg,
        ):
            raise AntiBotBlockedError(reason)

        normalized = dict(item)
        normalized["url"] = final_url
        normalized.setdefault("html", html)
        normalized.setdefault("status_code", page_status if page_status is not None else http_status)
        normalized.setdefault("error_message", err_msg)
        return normalized

    def fetch_markdown(
        self,
        url: str,
        timeout: float = 45.0,
        *,
        allow_live_turbomopar: bool = False,
    ) -> dict[str, Any]:
        """Fetch markdown representation via /md (or /crawl fallback on 404/405)."""
        validated = validate_heritage_url(url, allow_live_turbomopar=allow_live_turbomopar)
        try:
            http_status, data = self._request_json(
                "POST", "/md", payload={"url": validated}, timeout=timeout
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {404, 405}:
                raise
            crawled = self.crawl(validated, timeout=timeout, allow_live_turbomopar=allow_live_turbomopar)
            return {
                "url": crawled.get("url", validated),
                "markdown": crawled.get("markdown", ""),
                "status_code": crawled.get("status_code", 200),
                "success": crawled.get("success", True),
            }

        final_url = str(data.get("redirected_url") or data.get("url") or validated)
        validate_heritage_url(final_url, allow_live_turbomopar=allow_live_turbomopar)
        md_text = str(data.get("markdown") or data.get("fit_markdown") or "")
        err_msg = str(data.get("error_message") or data.get("error") or "")
        page_status = data.get("status_code")
        eff_status = int(page_status) if isinstance(page_status, int) and not isinstance(page_status, bool) else http_status
        if reason := detect_antibot_block(eff_status, md_text, err_msg):
            raise AntiBotBlockedError(reason)
        result = dict(data)
        result["url"] = final_url
        result.setdefault("markdown", md_text)
        result["status_code"] = eff_status
        return result
