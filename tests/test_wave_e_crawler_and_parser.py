"""Offline unit & integration tests for Wave E crawler, WARC vault, normalizer, and authority graph."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import urllib.parse
import warnings

from click.testing import CliRunner
import httpx
import pytest
import yaml

from logicaide.cli import main as cli
from logicaide.heritage.crawl4ai_client import (
    AntiBotBlockedError, Crawl4AIClient, HijackedDomainError, detect_antibot_block, load_crawl4ai_token, validate_heritage_url,
)
from logicaide.heritage.discovery import build_cdx_query_url
from logicaide.heritage.discovery_wave_e import (
    ALLPAR_CUTOFF_TO, ALLPAR_EXCLUDE_FORUMS_FILTER, WAVE_E_SPECS,
    extract_wave_e_fingerprints, validate_wave_e_query_pattern,
)
from logicaide.heritage.doc_curator import DeadboltViolationError
from logicaide.heritage.practitioner_graph import (
    CORE_ECU_PRACTITIONER, TRUSTED_DOMAIN_PEER, UNVERIFIED_COMMUNITY,
    build_practitioner_authority_graph, classify_thread_trust_tier, load_roster_overrides,
)
from logicaide.heritage.warc_vault import HeritageWarcVault, archive_figure, iter_warc_html_records, write_warc_record
from logicaide.heritage.wave_e_crawl import _normalize_url, run_wave_e_crawl
from logicaide.heritage.wave_e_pipeline import run_wave_e_parse_web
from logicaide.heritage.web_normalizer import (
    extract_clean_markdown, extract_frameset_urls, parse_forum_html, pre_ascii_table_to_gfm, stitch_forum_threads,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mock_client(handler) -> httpx.Client:
    """Create an offline httpx.Client backed by MockTransport with redirects disabled."""
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


# 1. Hijack blocklist (turbo-mopar.net & homemadeturbo.com)
@pytest.mark.parametrize("bad_url", [
    "http://turbo-mopar.net/forums/", "https://homemadeturbo.com/wiki",
    "https://sub.turbo-mopar.net/payload", "http://www.homemadeturbo.com/index.html",
    "https://web.archive.org/web/20150101120000id_/http://turbo-mopar.net/forums/",
    "https://web.archive.org/web/20140601id_/https://forum.homemadeturbo.com/t-1.html",
])
def test_hijack_blocklist_validate_heritage_url(bad_url: str) -> None:
    """Direct, subdomain, and Wayback inner hijacked URLs must raise HijackedDomainError."""
    with pytest.raises(HijackedDomainError):
        validate_heritage_url(bad_url)


def test_warc_vault_redirect_hop_to_hijacked_domain_never_fetches_target(tmp_path: Path) -> None:
    """_safe_get must abort before issuing a second GET when Location targets turbo-mopar.net."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://turbo-mopar.net/evil"})

    vault = HeritageWarcVault(tmp_path / "warc", tmp_path / "figures", http_client=_mock_client(handler), min_delay_s=0.0)
    with pytest.raises(HijackedDomainError):
        vault._safe_get("http://www.minimopar.net/start.html", allow_live_turbomopar=False)
    assert seen_urls == ["http://www.minimopar.net/start.html"]


def test_hijack_blocklist_frameset_query_pattern_and_crawl_isolation(tmp_path: Path) -> None:
    """Frameset extraction, Wave E pattern validation, and crawl loop enforce hijack blocklist."""
    html = (
        "<frameset><frame src='http://www.thedodgegarage.com/left.html'><frame src='http://turbo-mopar.net/evil.html'>"
        "<iframe src='https://web.archive.org/web/20150101id_/http://homemadeturbo.com/ad'></iframe></frameset>"
        "<a href='http://sub.homemadeturbo.com/x'>bad</a><a href='/turbo.html'>good</a>"
    )
    extracted = extract_frameset_urls(html, "http://www.thedodgegarage.com/", url_validator=validate_heritage_url)
    assert extracted["frame_urls"] == ["http://www.thedodgegarage.com/left.html"]
    assert extracted["page_links"] == ["http://www.thedodgegarage.com/turbo.html"]
    for pat in ("turbo-mopar.net/*", "sub.homemadeturbo.com/*", "web.archive.org/web/*/turbo-mopar.net/*"):
        with pytest.raises(ValueError, match="hijacked"):
            validate_wave_e_query_pattern(pat)

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if "minimopar.net" in str(request.url):
            return httpx.Response(200, headers={"Content-Type": "text/html"}, text=f"<html><title>M {p}</title><body><a href='http://turbo-mopar.net/evil'>X</a><a href='/ecu/'>E</a><p>{p}</p></body></html>")
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text=f"<html><title>DG {p}</title><body>SMEC {p}</body></html>")

    vault = HeritageWarcVault(tmp_path / "warc", tmp_path / "figures", http_client=_mock_client(handler), min_delay_s=0.0)
    summary = run_wave_e_crawl(
        sources=["minimopar", "thedodgegarage"], max_pages_per_source=2, queue_path=tmp_path / "no_q.json",
        warc_root=tmp_path / "warc", figures_root=tmp_path / "figures", manifest_path=None, fetch_images=False, delay_s=0.0, vault=vault,
    )
    assert summary["blocked_count"] >= 1 and any("turbo-mopar.net/evil" in b["url"] for b in summary["blocked"])
    assert summary["per_source"]["minimopar"]["captured_count"] == 2 and summary["per_source"]["thedodgegarage"]["captured_count"] == 2


# 2. Cloudflare & anti-bot circuit breaker
@pytest.mark.parametrize(("status", "html", "err", "expected"), [
    (307, "", "", True),
    (403, "<html><title>Attention Required! | Cloudflare</title></html>", "", True),
    (200, "<div class='cf-turnstile'>challenges.cloudflare.com/turnstile</div>", "", True),
    (200, "<p>Please verify you are human before continuing.</p>", "", True),
    (200, "<html><body><h1>SMEC 68HC11 Pinout</h1></body></html>", "", False),
])
def test_detect_antibot_block(status: int, html: str, err: str, expected: bool) -> None:
    """detect_antibot_block flags 307/403 Cloudflare, Turnstile, and CAPTCHA gates."""
    assert bool(detect_antibot_block(status, html, err)) is expected


def test_crawl4ai_antibot_circuit_breaker_and_live_turbomopar_guard() -> None:
    """Crawl4AIClient aborts immediately on Turnstile/403 and blocks live www.turbo-mopar.com."""
    calls: list[str] = []

    def cf_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(403, text="Cloudflare challenge cf-ray 1234")
        return httpx.Response(200, json={"results": [{"url": "https://www.minimopar.net/", "status_code": 200, "html": "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"}]})

    client = Crawl4AIClient(base_url="http://crawl4ai.local", api_token="test-tok", http_client=_mock_client(cf_handler))
    with pytest.raises(AntiBotBlockedError):
        client.crawl("https://www.minimopar.net/a")
    assert len(calls) == 1
    with pytest.raises(AntiBotBlockedError):
        client.crawl("https://www.minimopar.net/b")
    assert len(calls) == 2

    for fn in (lambda: validate_heritage_url("https://www.turbo-mopar.com/forums/"), lambda: client.crawl("https://www.turbo-mopar.com/forums/")):
        with pytest.raises(AntiBotBlockedError):
            fn()
    assert len(calls) == 2
    assert validate_heritage_url("https://www.turbo-mopar.com/forums/", allow_live_turbomopar=True).startswith("https://")
    assert validate_heritage_url("https://web.archive.org/web/20150101id_/http://www.turbo-mopar.com/forums/").startswith("https://")


# 3. Crawl4AIClient secrets.env posture, SSRF/redirect guards, and allpar.com CDX guards
def test_crawl4ai_secrets_env_token_header_and_no_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Token loads from 0600 secrets.env without warning, warns on 0644, rejects symlink, and never leaks."""
    monkeypatch.delenv("CRAWL4AI_API_TOKEN", raising=False)
    secret_val = "unit-test-bearer-xyz987"
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(f"# comment\nexport CRAWL4AI_API_TOKEN='{secret_val}'\n", encoding="utf-8")
    secrets_file.chmod(0o600)
    seen_auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_auth.append(request.headers.get("Authorization", ""))
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"results": [{"url": "https://sites.google.com/site/mpscilink", "status_code": 200, "html": "<html><title>mpscilink</title><body>62500 baud</body></html>"}]})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        c4ai = Crawl4AIClient(base_url="http://crawl4ai.local", secrets_path=secrets_file, http_client=_mock_client(handler))
        assert secret_val not in repr(c4ai) and secret_val not in json.dumps(c4ai.health())
        vault = HeritageWarcVault(tmp_path / "warc", tmp_path / "figures", crawl4ai_client=c4ai, http_client=_mock_client(handler), min_delay_s=0.0)
        archived = vault.fetch_and_archive("https://sites.google.com/site/mpscilink", "turbomopar", use_crawl4ai=True, fetch_images=False)
    assert not [w for w in caught if issubclass(w.category, UserWarning) and "0600" in str(w.message)]
    assert seen_auth == [f"Bearer {secret_val}", f"Bearer {secret_val}"] and secret_val not in json.dumps(archived)

    sym_sec = tmp_path / "sym_secrets.env"
    sym_sec.symlink_to(secrets_file)
    with pytest.raises(ValueError, match="symlink"):
        load_crawl4ai_token(sym_sec)
    secrets_file.chmod(0o644)
    with pytest.warns(UserWarning, match="0600"):
        assert load_crawl4ai_token(secrets_file) == secret_val


def test_ssrf_guards_and_crawl4ai_redirect_validation() -> None:
    """SSRF/private IPs/creds blocked; local-archive gated; crawl/fetch_markdown validate redirected_url & status."""
    for ssrf in (
        "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://192.168.3.173/health",
        "http://localhost/", "http://user:pass@minimopar.net/", "https://web.archive.org/web/20150101id_/http://127.0.0.1/",
        "http://local-archive.invalid/a",
    ):
        with pytest.raises(ValueError):
            validate_heritage_url(ssrf)
    assert validate_heritage_url("http://local-archive.invalid/a", allow_local_archive=True) == "http://local-archive.invalid/a"

    def redir_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/crawl":
            return httpx.Response(200, json={"results": [{"url": "https://www.minimopar.net/a", "redirected_url": "https://www.minimopar.net/b", "html": "<p>ok</p>"}]})
        mode = request.content.decode("utf-8")
        if "ssrf" in mode:
            return httpx.Response(200, json={"url": "https://www.minimopar.net/x", "redirected_url": "http://127.0.0.1/secret", "markdown": "x"})
        if "hijack" in mode:
            return httpx.Response(200, json={"url": "https://www.minimopar.net/x", "redirected_url": "http://turbo-mopar.net/evil", "markdown": "x"})
        return httpx.Response(200, json={"url": "https://www.minimopar.net/m", "status_code": 403, "markdown": "Cloudflare cf-ray"})

    c4ai = Crawl4AIClient(base_url="http://crawl4ai.local", api_token="t", http_client=_mock_client(redir_handler))
    assert c4ai.crawl("https://www.minimopar.net/a")["url"] == "https://www.minimopar.net/b"
    with pytest.raises(ValueError):
        c4ai.fetch_markdown("https://www.minimopar.net/ssrf")
    with pytest.raises(HijackedDomainError):
        c4ai.fetch_markdown("https://www.minimopar.net/hijack")
    with pytest.raises(AntiBotBlockedError):
        c4ai.fetch_markdown("https://www.minimopar.net/cf")


def test_allpar_cdx_guards_and_memento_filtering(tmp_path: Path) -> None:
    """Allpar specs enforce 20170531 cutoff, forum exclusion, root rejection, and crawl memento filtering."""
    allpar_specs = [s for s in WAVE_E_SPECS if s[0] == "allpar.com"]
    assert allpar_specs and all(s[4] == ALLPAR_CUTOFF_TO == "20170531" and ALLPAR_EXCLUDE_FORUMS_FILTER in s[5] for s in allpar_specs)
    fps = [r for r in extract_wave_e_fingerprints(repo_root=tmp_path) if r["source_site"] == "allpar.com"]
    assert all(r["to_timestamp"] == "20170531" and "!original:.*/forums/.*" in r["filter_exprs"] for r in fps)
    cdx_url = build_cdx_query_url("allpar.com/mopar/*", to_timestamp=ALLPAR_CUTOFF_TO, filter_exprs=(ALLPAR_EXCLUDE_FORUMS_FILTER,))
    assert "&to=20170531" in cdx_url and "!original:.*/forums/.*" in urllib.parse.unquote(cdx_url)
    for bad_pat in ("allpar.com/*", "allpar.com/forums/*"):
        with pytest.raises(ValueError, match="allpar.com"):
            validate_wave_e_query_pattern(bad_pat)

    cdx_queries, fetched_http = [], []

    def fake_cdx(url: str) -> list[dict[str, str]]:
        decoded = urllib.parse.unquote(url)
        cdx_queries.append(decoded)
        if "allpar.com/mopar/" not in decoded:
            return []
        return [
            {"timestamp": "20170601120000", "original": "http://www.allpar.com/mopar/post.html", "memento_url": "https://web.archive.org/web/20170601120000id_/http://www.allpar.com/mopar/post.html"},
            {"timestamp": "20160501120000", "original": "http://www.allpar.com/forums/showthread.php?t=5", "memento_url": "https://web.archive.org/web/20160501120000id_/http://www.allpar.com/forums/showthread.php?t=5"},
            {"timestamp": "20160501120000", "original": "http://www.allpar.com/mopar/smec.html", "memento_url": "https://web.archive.org/web/20160501120000id_/http://www.allpar.com/mopar/smec.html"},
        ]

    def http_handler(request: httpx.Request) -> httpx.Response:
        fetched_http.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html><title>SMEC</title><body>68HC11 SMEC</body></html>")

    vault = HeritageWarcVault(tmp_path / "warc", tmp_path / "figures", http_client=_mock_client(http_handler), min_delay_s=0.0)
    res = run_wave_e_crawl(
        sources=["allpar"], max_pages_per_source=5, queue_path=tmp_path / "no_q.json", warc_root=tmp_path / "warc",
        figures_root=tmp_path / "figures", manifest_path=None, fetch_images=False, fetch_cdx=True, delay_s=0.0, vault=vault, cdx_fetcher=fake_cdx,
    )
    assert all("to=20170531" in q and "!original:.*/forums/.*" in q for q in cdx_queries)
    assert fetched_http == ["https://web.archive.org/web/20160501120000id_/http://www.allpar.com/mopar/smec.html"] and res["captured_count"] == 1


# 4. HeritageWarcVault WARC roundtrip, figure HTML/off-host tolerance, close(), symlinks
def test_warc_vault_roundtrip_dedup_figures_and_symlinks(tmp_path: Path) -> None:
    """ISO 28500 .warc.gz roundtrip, reload dedup, off-host/HTML figure skip, close(), and symlink quarantine."""
    gif_bytes = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    jpg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
    html_body = (
        "<html><title>Pinout</title><body><img src='/img/pinout.gif'><a href='/img/board.jpg'>B</a>"
        "<img src='/img/pinout_dup.gif'><img src='https://evil.example.org/x.gif'><img src='/ecu/bad.gif'></body></html>"
    )
    seen_reqs: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_reqs.append(str(request.url))
        if request.url.path == "/ecu/bad.gif":
            return httpx.Response(302, headers={"Location": "/404.html"})
        if request.url.path == "/404.html":
            return httpx.Response(200, headers={"Content-Type": "text/html"}, text="<html>Not found</html>")
        if request.url.path.endswith(".gif"):
            return httpx.Response(200, headers={"Content-Type": "image/gif"}, content=gif_bytes)
        if request.url.path.endswith(".jpg"):
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=jpg_bytes)
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, text=html_body)

    warc_root, fig_root = tmp_path / "warc", tmp_path / "figures"
    injected_http = _mock_client(handler)
    vault = HeritageWarcVault(warc_root, fig_root, http_client=injected_http, min_delay_s=0.0)
    first = vault.fetch_and_archive("http://www.minimopar.net/ecu/", "minimopar", use_crawl4ai=False)
    assert first["status"] == "archived" and len(first["figures"]) == 2
    assert not any("evil.example.org" in u for u in seen_reqs) and (warc_root / "minimopar" / "capture_index.jsonl").is_file()
    recs = iter_warc_html_records(first["warc_path"])
    assert len(recs) == 1 and recs[0]["url"] == "http://www.minimopar.net/ecu/" and recs[0]["sha256"] == first["sha256"]
    vault.close()
    assert injected_http.is_closed is False

    owned_vault = HeritageWarcVault(warc_root, fig_root, min_delay_s=0.0)
    internal_http = owned_vault._get_http_client()
    owned_vault.close()
    assert internal_http.is_closed is True

    reloaded = HeritageWarcVault(warc_root, fig_root, http_client=_mock_client(handler), min_delay_s=0.0)
    assert reloaded.fetch_and_archive("http://www.minimopar.net/ecu/", "minimopar", use_crawl4ai=False)["deduplicated"] is True
    assert len(iter_warc_html_records(first["warc_path"])) == 1
    assert archive_figure(gif_bytes, fig_root / "minimopar", "gif_copy.gif", existing_hashes=vault._vault_hashes)["deduplicated"] is True
    assert archive_figure(jpg_bytes, fig_root / "minimopar", "board.jpg")["deduplicated"] is True

    qdir = tmp_path / "QUARANTINE_PII_DO_NOT_INGEST"
    qdir.mkdir()
    (qfile := qdir / "raw.html").write_text("<html><body>raw</body></html>", encoding="utf-8")
    (sym_q := tmp_path / "safe_alias.html").symlink_to(qfile)
    (sym_dir := tmp_path / "sym_warc").symlink_to(warc_root)
    with pytest.raises(ValueError):
        reloaded.ingest_local_archive(sym_q, "minimopar")
    with pytest.raises(ValueError, match="symlink"):
        run_wave_e_crawl(sources=["minimopar"], warc_root=sym_dir, figures_root=fig_root, vault=reloaded)
    with pytest.raises(ValueError, match="symlink"):
        run_wave_e_parse_web(warc_root=sym_dir, output_dir=tmp_path / "md", roster_path=tmp_path / "r.yaml")


# 5. web_normalizer: pre tables, trafilatura, framesets, forums, stitching, safety
def test_pre_ascii_table_to_gfm_and_extract_clean_markdown(tmp_path: Path) -> None:
    """GFM table conversion, fenced 68HC11 ASM, obfuscated email scrub, symlink & flash CLI rejection."""
    assert "| Pin | Signal | Notes |\n| --- | --- | --- |" in "\n".join(pre_ascii_table_to_gfm("Pin\tSignal\tNotes\n11\tSCI_RX\t62500 baud\n12\tSCI_TX\tECU out"))
    assert "| Addr | Table | Bytes |" in "\n".join(pre_ascii_table_to_gfm("Addr    Table     Bytes\n$8000   PEFTBL    16\n$8010   WOTTBL    16"))
    ruler_gfm = "\n".join(pre_ascii_table_to_gfm("Pin   Function     Volts\n---   ----------   -----\nA14   ASD Relay    12.0V\nK22   Fuel Pump    12.0V"))
    assert "| Pin | Function | Volts |" in ruler_gfm and "| A14 | ASD Relay | 12.0V |" in ruler_gfm
    asm_block = "\n".join(pre_ascii_table_to_gfm("        LDAA    $8000\n        STAA    $1030\n        RTS"))
    assert asm_block.startswith("```text") and "LDAA    $8000" in asm_block and "| ---" not in asm_block

    html = (
        "<html><body><article><h1>SMEC Calibration</h1><p>Contact alice (at) example.com for bench notes.</p>"
        "<pre>Pin\tSignal\tLevel\n11\tSCI\t5V\n22\tMAP\t0-5V</pre>"
        "<pre>LDAA $8000\nSTAA $1030</pre></article></body></html>"
    )
    md = extract_clean_markdown(html, label="smec_test")
    assert "| Pin | Signal | Level |" in md and "| 11 | SCI | 5V |" in md and "```text\nLDAA $8000" in md
    assert "[REDACTED_EMAIL]" in md and "alice" not in md and "example.com" not in md

    (real_h := tmp_path / "ok.html").write_text("<html><body><p>ok</p></body></html>", encoding="utf-8")
    (sym_h := tmp_path / "sym.html").symlink_to(real_h)
    with pytest.raises(ValueError, match="symlink"):
        extract_clean_markdown(sym_h)
    for bad_cli in ("minipro", "flash_ecu"):
        with pytest.raises(ValueError, match="live flash CLI"):
            extract_clean_markdown(f"<html><body><article><p>Run <code>{bad_cli}</code> now.</p></article></body></html>")


def test_parse_forum_html_and_stitch_multipage_threads() -> None:
    """Strip quotes/avatars/sigs across vBulletin, archive .posttext, phpBB, generic <article>; stitch ?s=/-p-2."""
    vb_html = (
        "<html><title>SMEC Pin 11 Fix - Page 2</title><body><div id='post102' class='vb_postbit'>"
        "<a class='bigusername'>GuruA</a><div class='thead'>01-02-2012</div><div id='post_message_102'>"
        "<img class='avatar' src='/avatar/1.gif'><blockquote><div>Originally Posted by NoobZ:</div>"
        "<blockquote>Originally Posted by OtherGuy: nested pyramid</blockquote>Where is pin 11?</blockquote>"
        "Measure Pin 11 at 5V with 68HC11 SMEC on bench; that fixed the no-start.<br>"
        "<div class='signature'>Sent from my garage</div></div></div></body></html>"
    )
    arch_html = (
        "<html><title>SMEC Pin 11 Fix - Archive</title><body><div class='post' id='p101'><div class='username'>OpStarter</div>"
        "<div class='date'>01-01-2012</div><div class='posttext'>SMEC no-start after 28C256 swap, checking Pin 11.</div></div></body></html>"
    )
    phpbb_html = (
        "<html><title>SBEC MapValue</title><body><div id='p55' class='post'><div class='postprofile'><dt><a>PeerB</a></dt></div>"
        "<div class='author'>by PeerB » Tue Mar 03, 2015</div><div class='content'><blockquote><cite>NoobZ wrote:</cite>Old quote text</blockquote>"
        "Confirmed MapValue at 0xF2 using MPScan 62500 baud.<div class='signature'>My sig</div></div></div></body></html>"
    )
    gen_html = (
        "<html><title>Generic</title><body><article id='a1'><header><strong>GenOp</strong><time>2016-04-05</time></header>"
        "Bench tested 68HC11 SCI at 62500 baud (alice [at] example.com).</article></body></html>"
    )

    _, _, norm_url = _normalize_url(
        "https://web.archive.org/web/20150101id_/http://turbo-mopar.com/forums/showthread.php?t=900&s=0123456789abcdef0123456789abcdef&page=2",
        strip_vb_session=True,
    )
    assert "s=" not in norm_url
    p2 = parse_forum_html(vb_html, url="https://web.archive.org/web/20150101id_/http://turbo-mopar.com/forums/archive/index.php/t-900-p-2.html")
    p1 = parse_forum_html(arch_html, url="https://web.archive.org/web/20150101id_/http://turbo-mopar.com/forums/archive/index.php/t-900.html")
    pb = parse_forum_html(phpbb_html, url="http://local-archive.invalid/forums/viewtopic.php?t=55")
    gen = parse_forum_html(gen_html, url="http://local-archive.invalid/forums/a1.html")

    assert p2["forum_engine"] == "vbulletin" and p2["page_num"] == 2
    assert "nested pyramid" not in p2["posts"][0]["body_text"] and "Sent from my garage" not in p2["posts"][0]["body_text"]
    assert "noobz" in [q.lower() for q in p2["posts"][0]["quoted_authors"]] and p1["posts"][0]["body_text"].startswith("SMEC no-start")
    assert pb["forum_engine"] == "phpbb" and pb["posts"][0]["author"] == "PeerB" and pb["posts"][0]["post_date"] == "Tue Mar 03, 2015"
    assert "My sig" not in pb["posts"][0]["body_text"] and "Old quote text" not in pb["posts"][0]["body_text"]
    assert gen["posts"][0]["author"] == "GenOp" and gen["posts"][0]["post_date"] == "2016-04-05"
    assert "[REDACTED_EMAIL]" in gen["posts"][0]["body_text"] and "alice" not in gen["posts"][0]["body_text"]

    stitched = stitch_forum_threads([p2, p1])
    assert len(stitched) == 1 and stitched[0]["page_numbers"] == [1, 2] and [p["author"] for p in stitched[0]["posts"]] == ["OpStarter", "GuruA"]
    assert "Question / Topic -> Verified Resolution" in stitched[0]["summary_markdown"] and "## Verified Resolution" in stitched[0]["summary_markdown"]


# 6. practitioner_graph: 3 passes, pin/mute comments/notes, PII & Deadbolt
def test_practitioner_graph_passes_comments_pii_and_deadbolt(tmp_path: Path) -> None:
    """Pass 1 proof threshold, Pass 2 1-hop vs dispute/2-hop, Pass 3 pin/mute persistence, PII/Deadbolt."""
    roster_path = tmp_path / "practitioner_roster.yaml"
    threads = [{"thread_id": "t1", "title": "SMEC 68HC11 PEFTBL", "posts": [
        {"post_id": "1", "author": "CoreWizard", "body": "In ChemAsm, LDAA $8000 and STAA $1030 load PEFTBL from 28C256."},
        {"post_id": "2", "author": "HeadGuru", "body": "Flowbench on #782 head showed 195 cfm at 0.500 lift."},
        {"post_id": "3", "author": "CoreWizard", "body": "Thanks @HeadGuru, your #782 flowbench numbers are verified spot on."},
        {"post_id": "4", "author": "BadPeer", "body": "A555 gear ratios are 3.85 overall."},
        {"post_id": "5", "author": "CoreWizard", "body": "@BadPeer is wrong and unverified about A555 ratios. [quote]@BadPeer confirmed[/quote]"},
        {"post_id": "6", "author": "HeadGuru", "body": "Thanks @SecondHop, your TE04H boost numbers are verified."},
        {"post_id": "7", "author": "SecondHop", "body": "TE04H spool at 2800 rpm with 15 psi boost."},
        {"post_id": "8", "author": "NoobHelp", "body": "My car will not start, where is the diagnostic port?"},
    ]}]
    g1 = build_practitioner_authority_graph(threads, roster_path=roster_path)
    h1 = g1["handles"]
    assert h1["CoreWizard"]["trust_tier"] == CORE_ECU_PRACTITIONER and h1["HeadGuru"]["trust_tier"] == TRUSTED_DOMAIN_PEER
    assert h1["BadPeer"]["trust_tier"] == UNVERIFIED_COMMUNITY and h1["SecondHop"]["trust_tier"] == UNVERIFIED_COMMUNITY
    assert h1["NoobHelp"]["trust_tier"] == UNVERIFIED_COMMUNITY and classify_thread_trust_tier(threads[0], g1) == "Tier 2"
    assert classify_thread_trust_tier({"title": "Help", "posts": [{"author": "NoobHelp", "body": "Won't start"}]}, g1) == "Tier 3"

    patched = "# Manual top operator note\n" + roster_path.read_text(encoding="utf-8").replace(
        "  HeadGuru:\n", "  # Verified cylinder head contributor\n  HeadGuru:  # inline-tag\n    notes: 'Verified #782 head flowbench logs'\n",
    )
    roster_path.write_text(patched, encoding="utf-8")
    g2 = build_practitioner_authority_graph(threads, roster_path=roster_path, pin_handles=["SecondHop"], mute_handles=["CoreWizard"])
    assert g2["handles"]["SecondHop"]["trust_tier"] == CORE_ECU_PRACTITIONER and g2["handles"]["SecondHop"]["pinned"]
    assert g2["handles"]["CoreWizard"]["muted"] and g2["handles"]["HeadGuru"]["trust_tier"] == UNVERIFIED_COMMUNITY
    rebuilt_text = roster_path.read_text(encoding="utf-8")
    for expected in ("# Manual top operator note", "# Verified cylinder head contributor", "# inline-tag", "Verified #782 head flowbench logs"):
        assert expected in rebuilt_text
    assert load_roster_overrides(roster_path) == {"CoreWizard": "mute", "SecondHop": "pin"}

    empty_roster = tmp_path / "pii_roster.yaml"
    pii_graph = build_practitioner_authority_graph(
        [{"post_id": "p9", "author": "alice [at] example.com", "body": "See /home/alice/secret.txt and bucar notes"}],
        roster_path=empty_roster, save_roster=False,
    )
    pii_rec = next(iter(pii_graph["handles"].values()))
    assert "alice" not in pii_rec["handle"].lower() and "[REDACTED_EMAIL]" in pii_rec["handle"]
    assert "/home/alice" not in pii_rec["sample_snippets"][0] and "bucar" not in pii_rec["sample_snippets"][0].lower()

    with pytest.raises(DeadboltViolationError):
        build_practitioner_authority_graph([{"post_id": "p10", "author": "BadTool", "body": "Write " + "0x" + "41 to " + "/dev/" + "ttyUSB0 via " + "Flash" + ".frm"}], roster_path=empty_roster, save_roster=False)
    with pytest.raises(ValueError, match="live flash CLI"):
        build_practitioner_authority_graph([{"post_id": "p11", "author": "CliTool", "body": "Run minipro or flash_ecu"}], roster_path=empty_roster, save_roster=False)


# 7. CDX scheme guard, poisoned meta isolation, E2E CLI (`crawl` + `parse-web`), .gitignore
def test_cdx_scheme_guard_and_poisoned_meta_author_isolation(tmp_path: Path) -> None:
    """Non-Wayback cdx_query_url is logged in errors; poisoned meta author is isolated from valid docs."""
    (q_path := tmp_path / "bad_cdx.json").write_text(json.dumps({"queue": [{"source_site": "allpar.com", "cdx_query_url": "file:///etc/passwd"}]}), encoding="utf-8")
    vault = HeritageWarcVault(tmp_path / "warc", tmp_path / "figures", http_client=_mock_client(lambda r: httpx.Response(200)), min_delay_s=0.0)
    res = run_wave_e_crawl(sources=["allpar"], max_pages_per_source=1, queue_path=q_path, warc_root=tmp_path / "warc", figures_root=tmp_path / "figures", manifest_path=None, fetch_images=False, fetch_cdx=True, delay_s=0.0, vault=vault)
    assert res["error_count"] >= 1 and any("file:///etc/passwd" in e["url"] for e in res["errors"])

    (local_dir := tmp_path / "local_html").mkdir()
    (local_dir / "good.html").write_text("<html><title>Good</title><body><p>Clean SMEC 68HC11.</p></body></html>", encoding="utf-8")
    (local_dir / "poison.html").write_text("<html><head><meta name='author' content='minipro'></head><title>Bad</title><body><p>x</p></body></html>", encoding="utf-8")
    summary = run_wave_e_parse_web(warc_root=tmp_path / "warc", output_dir=tmp_path / "out_md", roster_path=tmp_path / "r.yaml", local_archive_dirs=[local_dir])
    assert summary["static_docs_written"] == 1 and len(summary["errors"]) == 1


def test_e2e_cli_crawl_parse_web_and_gitignore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end Click CliRunner for heritage crawl & parse-web plus .gitignore verification."""
    warc_root, fig_root, out_dir = tmp_path / "warc", tmp_path / "figures", tmp_path / "web_md"
    roster_path, q_path = tmp_path / "roster.yaml", tmp_path / "queue.json"
    q_path.write_text('{"queue": []}', encoding="utf-8")
    handler = lambda req: httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, text=f"<html><title>MiniMopar {req.url.path}</title><body><h1>SMEC</h1><p>Pin 11 SCI {req.url.path}.</p></body></html>")
    monkeypatch.setattr(HeritageWarcVault, "_get_http_client", lambda self: _mock_client(handler))
    runner = CliRunner()
    crawl_res = runner.invoke(cli, ["heritage", "crawl", "--source", "minimopar", "--max-pages", "2", "--queue-path", str(q_path), "--warc-root", str(warc_root), "--figures-root", str(fig_root), "--no-images", "--delay", "0", "--json"])
    assert crawl_res.exit_code == 0 and json.loads(crawl_res.output)["captured_count"] == 2

    forum_html = (
        "<html><title>SMEC Pin 11 Thread</title><body><div id='post1' class='vb_postbit'><a class='bigusername'>PinnedGuru</a>"
        "<div id='post_message_1'>LDAA $8000 PEFTBL ChemAsm 28C256 verified fix.</div></div>"
        "<div id='post2' class='vb_postbit'><a class='bigusername'>NoisyBot</a><div id='post_message_2'>Ignore this spam.</div></div></body></html>"
    ).encode("utf-8")
    write_warc_record(warc_root / "turbomopar" / "turbomopar.warc.gz", "https://web.archive.org/web/20150101id_/http://turbo-mopar.com/forums/showthread.php?t=777", 200, {"Content-Type": "text/html; charset=utf-8"}, forum_html)
    parse_res = runner.invoke(cli, ["heritage", "parse-web", "--warc-root", str(warc_root), "--output-dir", str(out_dir), "--roster-path", str(roster_path), "--pin", "PinnedGuru", "--mute", "NoisyBot", "--json"])
    assert parse_res.exit_code == 0
    pj = json.loads(parse_res.output)
    assert pj["static_docs_written"] == 2 and pj["forum_threads_written"] == 1 and pj["tier2_threads"] == 1 and (out_dir / "parse_manifest.json").is_file()
    assert yaml.safe_load(roster_path.read_text(encoding="utf-8"))["overrides"] == {"NoisyBot": "mute", "PinnedGuru": "pin"}

    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/heritage_raw/**" in gitignore and "**/*.sqlite" in gitignore and "**/*.sqlite3" in gitignore
    for ignored_sample in ("data/heritage_raw/warc/test.warc.gz", "tmp_test.sqlite", "sub/tmp_test.sqlite3"):
        assert subprocess.run(["git", "check-ignore", "-q", ignored_sample], cwd=REPO_ROOT, check=False).returncode == 0
