"""Wave E HTML, <pre> ASCII table, frameset, and forum thread normalizer for LogicAIDE."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable
import urllib.parse

from bs4 import BeautifulSoup, Comment, Tag
import trafilatura

from logicaide.heritage.doc_curator import _format_gfm_pipe_table, assert_no_deadbolt_tokens, check_quarantine, html_table_to_gfm, scrub_pii

HIJACKED_DOMAINS: frozenset[str] = frozenset({"turbo-mopar.net", "homemadeturbo.com"})
MAX_PRE_TABLE_COLS = 14
_BLOCKED_SCHEMES = ("javascript:", "mailto:", "data:", "vbscript:", "tel:")
_WAYBACK_RE = re.compile(r"^/web/(\d+(?:[a-z_]+)?|\*)/(.+)$", re.I)
_RULER_LINE_RE = re.compile(r"^[\s\-=_+|]+$")
_CODE_LINE_RE = re.compile(r"^(?:#\s*(?:include|define|ifdef|pragma)\b|//|/\*|\s*(?:void|int|const|static|struct|if|for|while|return)\b.*[{;]|.*;\s*$)")
_ASM_LINE_RE = re.compile(
    r"^\s*(?:[A-Za-z_]\w*:)?\s*(?:LDAA|LDAB|LDD|LDX|LDY|STAA|STAB|STD|STX|STY|ADDA|ADDB|ADDD|SUBA|SUBB|SUBD|"
    r"CMPA|CMPB|CPD|CPX|CPY|BNE|BEQ|BCC|BCS|BRA|BSR|BHI|BLS|BMI|BPL|JSR|JMP|RTS|RTI|SWI|NOP|PSHA|PULA|TSTA|"
    r"TSTB|CLRA|CLRB|INCA|DECA|BITA|BITB|ANDA|ORAA|MOVLW|MOVWF|MOVF|RETLW|GOTO|CALL|BTFSC|BTFSS|BSF|BCF|"
    r"SUBWF|ADDWF|INCF|DECF|ORG|EQU|FCB|FDB|FCC|RMB|LIST)\b(?:\s|$)",
    re.I,
)
_SENTENCE_BREAK_RE = re.compile(r"[a-z]{3,}[.!?]\s{2,}[A-Z]")
_TITLE_SUFFIX_RE = re.compile(r"\s*(?:::|\||-|—)\s*(?:View topic|Page \d+|Archive|Printable Version|Thread).*$", re.I)
_QUOTE_AUTHOR_PATS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Originally\s+Posted\s+by\s+([^\n\r:<>\[\]]+?)(?:\s*(?:View Post|on\s+\d|at\s+\d|:|$))", re.I | re.M),
    re.compile(r"^([A-Za-z0-9_.\- ]{2,48}?)\s+(?:wrote|said)\s*:", re.I | re.M),
    re.compile(r"^Quote\s+from:\s*([^\n\r|]+?)(?:\s+on\s+|$)", re.I | re.M),
)
_RES_POS_RE = re.compile(r"\b(?:solved|fixed|worked|verified|confirmed|that did it|root cause|cured|resolved)\b", re.I)
_TECH_DET_RE = re.compile(r"\b(?:pin\s*\d+|ohm|volt|vdc|psi|kpa|map|tps|smec|sbec|lm|2n|0x[0-9a-f]+|fsm|ground)\b", re.I)
_OBF_EMAIL_RE = re.compile(r"\b[\w.+-]+\s*(?:\[at\]|\(at\))\s*[\w.-]+\s*(?:\[dot\]|\(dot\)|\.)\s*[a-z]{2,}\b", re.I)
_LIVE_FLASH_CLI_RE = re.compile(r"\b(?:minipro|flash_ecu)\b", re.I)


def scrub_all_pii(text: str) -> str:
    """Scrub standard PII plus mixed obfuscated [at]/(at) and [dot]/(dot)/. email patterns."""
    return _OBF_EMAIL_RE.sub("[REDACTED_EMAIL]", scrub_pii(text))


def enforce_heritage_safety(text: str, *, label: str) -> str:
    """Scrub all PII and reject Deadbolt tokens or live flash CLI commands."""
    cleaned = scrub_all_pii(text)
    assert_no_deadbolt_tokens(cleaned, label=label)
    if m := _LIVE_FLASH_CLI_RE.search(cleaned):
        raise ValueError(f"Hardware Deadbolt violation (live flash CLI) in {label}: {m.group(0)!r}")
    return cleaned


_safety_gate = enforce_heritage_safety


def _add_unique(items: list[str], seen: set[str], value: str, *, casefold: bool = False) -> None:
    """Append non-empty value to items preserving order if not yet in seen."""
    val = value.strip()
    key = val.lower() if casefold else val
    if val and key not in seen:
        seen.add(key)
        items.append(val)


def _host_matches(host: str, domain: str) -> bool:
    """Return True if host matches domain exactly or as a subdomain."""
    h, d = host.strip().lower().rstrip("."), domain.strip().lower().rstrip(".")
    return bool(h) and (h == d or h.endswith(f".{d}"))


def _resolve_safe_url(raw_url: str, base_url: str, *, url_validator: Callable[[str], Any] | None = None) -> str | None:
    """Resolve raw_url via urljoin and reject hijacked domains, unsafe schemes, and # fragments."""
    raw = (raw_url or "").strip()
    if not raw or raw.startswith("#") or re.sub(r"[\x00-\x20]+", "", raw).lower().startswith(_BLOCKED_SCHEMES):
        return None
    resolved = urllib.parse.urljoin(base_url, raw) if base_url else raw
    parsed = urllib.parse.urlparse(resolved)
    scheme, host = parsed.scheme.lower(), parsed.hostname or ""
    if (scheme and scheme not in {"http", "https"}) or (not scheme and base_url.strip()) or any(_host_matches(host, d) for d in HIJACKED_DOMAINS):
        return None
    if _host_matches(host, "web.archive.org") and (wb := _WAYBACK_RE.match(parsed.path or "")):
        inner = wb.group(2) if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", wb.group(2)) else f"http://{wb.group(2).lstrip('/')}"
        if any(_host_matches(urllib.parse.urlparse(inner).hostname or "", d) for d in HIJACKED_DOMAINS):
            return None
    if url_validator is not None:
        try:
            verdict = url_validator(resolved)
        except (ValueError, RuntimeError):
            return None
        return (verdict or None) if isinstance(verdict, str) else (resolved if verdict else None)
    return resolved


def _looks_like_code_or_asm(lines: list[str]) -> bool:
    """Detect C/Arduino code or 68HC11/PIC assembly blocks that must stay fenced."""
    if any(ln.lstrip().startswith((";", "//", "/*", "#include", "#define")) for ln in lines):
        return True
    code_hits = sum(1 for ln in lines if _CODE_LINE_RE.match(ln))
    asm_hits = sum(1 for ln in lines if _ASM_LINE_RE.match(ln))
    return (code_hits >= 2 or (len(lines) == 1 and code_hits == 1)) or (asm_hits >= 2 and asm_hits * 2 >= len(lines))


def _is_ruler(line: str) -> bool:
    """Return True if line consists solely of ASCII ruler/border characters."""
    return bool(_RULER_LINE_RE.match(line)) and any(ch in line for ch in ("-", "=", "_"))


def _parse_pipe_pre_table(raw_lines: list[str]) -> list[list[str]] | None:
    """Parse pipe-delimited <pre> lines into 2D cell rows when tabular."""
    non_sep = [ln for ln in raw_lines if not (_is_ruler(ln) and ("|" in ln or "+" in ln))]
    if len(non_sep) < 2 or not all(re.search(r"(?<!\|)\|(?!\|)", ln) for ln in non_sep) or _looks_like_code_or_asm(non_sep):
        return None
    rows = [[re.sub(r"\s+", " ", c).strip() for c in re.split(r"(?<!\\)\|", ln.strip().removeprefix("|").removesuffix("|"))] for ln in non_sep]
    counts = [len(r) for r in rows]
    return rows if min(counts) >= 2 and max(counts) <= MAX_PRE_TABLE_COLS else None


def _parse_ruler_pre_table(raw_lines: list[str]) -> list[list[str]] | None:
    """Infer fixed character column slices from an ASCII ruler divider line."""
    spans, has_box = None, False
    for ln in raw_lines:
        if _is_ruler(ln) and re.search(r"[-=_]{2,}", ln):
            found = [(m.start(), m.end()) for m in re.finditer(r"[-=_]+", ln)]
            if 2 <= len(found) <= MAX_PRE_TABLE_COLS:
                spans, has_box = found, ("+" in ln or "|" in ln)
                break
    data_lines = [ln for ln in raw_lines if not _is_ruler(ln)]
    if spans is None or len(data_lines) < 2:
        return None
    cuts = [0] + [
        ns if pe >= ns else min(range(pe, ns + 1), key=lambda c: (sum(1 for ln in data_lines if 0 < c < len(ln) and not ln[c - 1].isspace() and not ln[c].isspace()), abs(ns - c)))
        for (_, pe), (ns, _) in zip(spans, spans[1:])
    ]
    rows = []
    for ln in data_lines:
        cells = [re.sub(r"\s+", " ", ln[s : cuts[i + 1] if i + 1 < len(cuts) else None].strip().strip("|" if has_box else "").strip()) for i, s in enumerate(cuts)]
        if any(cells):
            rows.append(cells)
    return rows if len(rows) >= 2 else None


def pre_ascii_table_to_gfm(pre_text: str) -> list[str]:
    """Convert tabular <pre> ASCII blocks to GFM pipe tables; non-tabular blocks stay fenced."""
    raw_lines = [ln.rstrip() for ln in pre_text.replace("\xa0", " ").splitlines() if ln.strip()]
    if not raw_lines:
        return []
    if pipe_rows := _parse_pipe_pre_table(raw_lines):
        return _format_gfm_pipe_table(pipe_rows)
    if ruler_rows := _parse_ruler_pre_table(raw_lines):
        return _format_gfm_pipe_table(ruler_rows)
    cand = [ln for ln in raw_lines if not _is_ruler(ln)]
    if cand and not _looks_like_code_or_asm(cand):
        rows = [[re.sub(r"\s+", " ", c).strip() for c in re.split(r"\t+|\s{2,}", ln.strip())] for ln in cand]
        counts = [len(r) for r in rows]
        sent = sum(1 for ln in cand if "\t" not in ln and _SENTENCE_BREAK_RE.search(ln))
        if len(rows) >= 2 and min(counts) >= 2 and max(counts) <= MAX_PRE_TABLE_COLS and not (max(counts) == 2 and sent * 2 > len(cand)):
            return _format_gfm_pipe_table(rows)
    return ["```text", *raw_lines, "```"]


def replace_pre_and_html_tables(soup: BeautifulSoup) -> BeautifulSoup:
    """Replace non-nested <table> and <pre> tags in soup with GFM pipe tables or fenced blocks."""
    for table in list(soup.find_all("table")):
        if table.find_parent("table") is not None or (table.find("pre") is not None and len(table.find_all("tr")) <= 2 and len(table.find_all(["td", "th"])) <= 2):
            continue
        if gfm := html_table_to_gfm(table):
            table.replace_with(soup.new_string("\n\n" + "\n".join(gfm) + "\n\n"))
    for pre in list(soup.find_all("pre")):
        if gfm := pre_ascii_table_to_gfm(pre.get_text("\n")):
            pre.replace_with(soup.new_string("\n\n" + "\n".join(gfm) + "\n\n"))
    return soup


def _extract_block_text(root: Tag) -> str:
    """Extract block-structured text from BeautifulSoup without splitting inline tags."""
    for br in root.find_all("br"):
        br.replace_with("\n")
    for blk in root.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
        blk.insert_after("\n\n")
    out, in_fence = [], False
    for ln in root.get_text().splitlines():
        st = ln.strip()
        if st.startswith("```"):
            in_fence = not in_fence
            out.append(st)
        else:
            out.append(ln.rstrip() if in_fence else re.sub(r"[ \t]+", " ", ln).strip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def extract_clean_markdown(source: Path | str | BeautifulSoup, *, label: str = "html_input") -> str:
    """Replace tables/<pre> inside a BS4 copy, extract clean Markdown, and enforce safety gates."""
    if isinstance(source, BeautifulSoup):
        html_text = str(source)
    elif isinstance(source, Path) or (isinstance(source, str) and "<" not in source and "\n" not in source and Path(source).suffix.lower() in {".htm", ".html", ".xhtml"}):
        if Path(source).is_symlink():
            raise ValueError(f"Refusing symlink archive source: {source}")
        path = check_quarantine(source)
        check_quarantine(path.resolve())
        html_text, label = path.read_text(encoding="utf-8", errors="ignore"), path.name
    else:
        html_text = str(source)

    soup = BeautifulSoup(html_text, "lxml")
    for junk in soup(["script", "style", "noscript"]):
        junk.decompose()
    replace_pre_and_html_tables(soup)
    placeholders: dict[str, str] = {}
    for node in list(soup.find_all(string=True)):
        if isinstance(node, Comment):
            continue
        text = str(node).strip()
        if (text.startswith("| ") and "\n| ---" in text) or (text.startswith("```") and text.endswith("```")):
            token = f"LOGICAIDEGFM{len(placeholders)}TOKEN"
            placeholders[token] = text
            p_tag = soup.new_tag("p")
            p_tag.string = token
            node.replace_with(p_tag)

    extracted = trafilatura.extract(str(soup), output_format="markdown", include_tables=True, include_formatting=True, favor_recall=True)
    if not extracted or any(tok not in extracted for tok in placeholders):
        body = soup.body or soup
        extracted = _extract_block_text(body) if isinstance(body, Tag) else ""
    for tok, block in placeholders.items():
        extracted = extracted.replace(tok, f"\n\n{block}\n\n")
    return _safety_gate(re.sub(r"\n{3,}", "\n\n", extracted).strip(), label=label)


def extract_frameset_urls(html: str, base_url: str, *, url_validator: Callable[[str], Any] | None = None) -> dict[str, list[str]]:
    """Extract and resolve frame, page, and image URLs while rejecting hijacked/unsafe links."""
    soup = BeautifulSoup(html, "lxml")
    docs: list[BeautifulSoup | Tag] = [soup, *(BeautifulSoup(nf.get_text(), "lxml") for nf in soup.find_all("noframes") if nf.get_text())]
    result: dict[str, list[str]] = {"frame_urls": [], "page_links": [], "image_urls": []}
    seen: dict[str, set[str]] = {k: set() for k in result}
    for doc in docs:
        for bucket, tags, attrs in (("frame_urls", ["frame", "iframe"], ("src", "data-src")), ("page_links", ["a", "area"], ("href",)), ("image_urls", ["img"], ("src", "data-src"))):
            for tag in doc.find_all(tags):
                if raw := next((tag.get(a) for a in attrs if tag.get(a)), None):
                    if safe := _resolve_safe_url(str(raw), base_url, url_validator=url_validator):
                        _add_unique(result[bucket], seen[bucket], safe)
    return result


def _canon_title(raw_title: str) -> str:
    """Strip forum page/archive suffixes and collapse whitespace."""
    title = _TITLE_SUFFIX_RE.sub("", re.sub(r"\s+", " ", raw_title).strip())
    return re.sub(r"\s*[\[(]Page\s*\d+[\])]\s*$", "", title, flags=re.I).strip()


def _extract_thread_metadata(soup: BeautifulSoup, url: str) -> tuple[str, str, int]:
    """Extract canonical thread_title, thread_id, and page_num from HTML and URL."""
    raw_t = next((el.get_text(" ", strip=True) for sel in ("h1", "h2.topic-title", ".maintitle", ".threadtitle", "title") if (el := soup.select_one(sel)) and el.get_text(strip=True)), "")
    title = _canon_title(raw_t)
    parsed = urllib.parse.urlparse(url) if url else None
    qs = urllib.parse.parse_qs(parsed.query) if parsed else {}
    tid = next((qs[k][0].split(".", 1)[0].strip() for k in ("t", "thread", "threadid", "topic", "tid") if qs.get(k)), "")
    if not tid and parsed:
        tid = next((m.group(1) for pat in (r"[./_-](?:t|thread|topic)[/_-]?(\d+)", r"/threads/[^/]*\.(\d+)", r"/(\d+)(?:-[^/]+)?\.html?$") if (m := re.search(pat, parsed.path, re.I))), "")
    if not tid and title:
        tid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64]

    pg_num = 1
    if pg := next((qs[k][0] for k in ("page", "pg", "pagenum") if qs.get(k) and qs[k][0].isdigit()), None):
        pg_num = max(1, int(pg))
    elif (el := soup.select_one(".pagination strong, .pagination .active, .pagenav strong, [aria-current='page']")) and (m := re.search(r"\b(\d+)\b", el.get_text(strip=True))):
        pg_num = max(1, int(m.group(1)))
    elif url and (m := re.search(r"(?:(?<![a-z])page[/_-]?(\d+)|-p-(\d+)\.html?)", url, re.I)):
        pg_num = max(1, int(m.group(1) or m.group(2)))
    elif qs.get("start") and qs["start"][0].isdigit():
        pg_num = (int(qs["start"][0]) // 15) + 1
    return title, tid, pg_num


def _strip_quotes_and_noise(body_tag: Tag) -> list[str]:
    """Capture quoted_authors from quote boxes, then strip quotes, avatars, sigs, and nav noise."""
    quoted, seen_q, quote_nodes = [], set(), []
    for tag in body_tag.find_all(True):
        if tag is body_tag or tag.parent is None:
            continue
        cls = set(tag.get("class") or [])
        if (tag.name == "blockquote" and "postcontent" not in cls) or (cls & {"quote", "quotecontent", "quotebox", "bbcode_quote"}) or (
            tag.name in {"td", "div"} and re.match(r"^\s*(?:Quote:\s*)?Originally\s+Posted\s+by\b", tag.get_text(" ", strip=True), re.I)
        ):
            tbl = tag.find_parent("table") if tag.name == "td" else None
            quote_nodes.append(tbl if (tbl is not None and tbl is not body_tag and body_tag in tbl.parents) else tag)
    for node in quote_nodes:
        if node.parent is None:
            continue
        for attr in ("data-author", "data-quote", "cite"):
            if (val := str(node.get(attr, "")).strip()) and not val.startswith(("http://", "https://")) and len(val) <= 64:
                _add_unique(quoted, seen_q, val.strip(" :,-"), casefold=True)
        for cite in node.find_all("cite"):
            _add_unique(quoted, seen_q, re.sub(r"\s+(?:wrote|said)\s*:?$", "", cite.get_text(" ", strip=True), flags=re.I).strip(" :,-"), casefold=True)
        block_txt = _extract_block_text(node)
        for pat in _QUOTE_AUTHOR_PATS:
            for m in pat.finditer(block_txt):
                if len(name := re.sub(r"\s+", " ", m.group(1)).strip(" :,-")) <= 64:
                    _add_unique(quoted, seen_q, name, casefold=True)
        if (prev := node.find_previous_sibling()) is not None and re.fullmatch(r"Quote:?", prev.get_text(" ", strip=True), re.I):
            prev.decompose()
        node.decompose()
    for el in body_tag.select("script,style,noscript,nav,footer,aside,.nav,.navbar,.breadcrumb,.breadcrumbs,.pagination,.pagenav,.avatar,.signature,.sig,[id^='sig'],.postbuttons,.back2top,.edit-notice"):
        if el is not body_tag and el.parent is not None:
            el.decompose()
    for img in list(body_tag.find_all("img")):
        if "avatar" in " ".join(str(img.get(a, "")) for a in ("class", "alt", "src")).lower():
            img.decompose()
    return quoted


def _clean_post_body(body_tag: Tag, base_url: str, *, url_validator: Callable[[str], Any] | None = None) -> tuple[str, list[str], list[str]]:
    """Strip quotes/noise, normalize tables, extract links, and return clean body_text."""
    quoted = _strip_quotes_and_noise(body_tag)
    seen_q, links, seen_links = {q.lower() for q in quoted}, [], set()
    for a in body_tag.find_all("a"):
        if safe := _resolve_safe_url(str(a.get("href", "")), base_url, url_validator=url_validator):
            _add_unique(links, seen_links, safe)
    replace_pre_and_html_tables(body_tag)
    kept: list[str] = []
    for ln in _extract_block_text(body_tag).splitlines():
        st = ln.strip()
        if re.match(r"^\s*(?:_{4,}|--)\s*$", ln):
            break
        if re.match(r"^(?:Back to top|Report this post|Last edited by\b)", st, re.I) or st.startswith(">") or re.fullmatch(r"Quote:?", st, re.I):
            continue
        if (m := re.match(r"^Originally\s+Posted\s+by\s+([^\n\r:]+?)\s*$", st, re.I)) or (m := re.match(r"^([A-Za-z0-9_.\- ]{2,48}?)\s+(?:wrote|said)\s*:\s*$", st, re.I)):
            _add_unique(quoted, seen_q, m.group(1), casefold=True)
            continue
        kept.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip(), quoted, links


def _norm_pid(raw_id: Any) -> str:
    """Normalize DOM post element ID into a canonical post_id string."""
    return re.sub(r"^(?:post_?|td_post_|post_message_|p)(?=\d+$)", "", str(raw_id or "").strip(), flags=re.I)


def _extract_phpbb_or_vb_posts(soup: BeautifulSoup) -> tuple[str, list[dict[str, Any]]] | None:
    """Extract post records from vBulletin or phpBB 2/3 DOM structures when detected."""
    if vb_boxes := [
        el for el in soup.select(".vb_postbit, li.postbitlegacy, li.postbit, table[id^='post'], div[id^='post'], li[id^='post']")
        if (re.fullmatch(r"post_?\d+", str(el.get("id") or "")) or ({"vb_postbit", "postbitlegacy", "postbit"} & set(el.get("class") or ())))
        and (el.find(class_="bigusername") or el.find(id=re.compile(r"^post_message_")))
    ]:
        items, seen = [], set()
        for box in vb_boxes:
            if any(id(p) in seen for p in box.parents):
                continue
            seen.add(id(box))
            u_el = box.find(class_="bigusername") or box.find(class_="username")
            u_td = u_el.find_parent("td") if u_el else None
            if (body := box.find(id=re.compile(r"^post_message_")) or next((c for c in box.select(".alt1, .alt2") if c is not u_td and (u_el is None or u_el not in c.descendants)), None)) is not None:
                dates = list(dict.fromkeys(td.get_text(" ", strip=True) for td in box.select(".postdate, .date, .thead") if td.get_text(strip=True) and not re.match(r"^(?:#\d+|Post\s*#\d+|Permalink)$", td.get_text(strip=True), re.I)))
                items.append({"post_id": _norm_pid(box.get("id") or body.get("id")), "author": u_el.get_text(" ", strip=True) if u_el else "", "post_date": " ".join(dates).strip(), "body_tag": body})
        if items:
            return "vbulletin", items
    if pb3 := [b for b in soup.select("div.post") if b.select_one(".content, .postbody")]:
        items = []
        for box in pb3:
            body, auth_el, date_txt = box.select_one(".content") or box.select_one(".postbody"), box.select_one(".postprofile dt a, .postprofile dt, .author strong, .name"), ""
            author_txt = auth_el.get_text(" ", strip=True) if auth_el else ""
            if auth_p := box.select_one(".author"):
                raw_a = auth_p.get_text(" ", strip=True)
                date_txt = raw_a.split("»", 1)[1].strip() if "»" in raw_a else (m.group(1).strip() if (m := re.search(r"\bon\s+(.+)$", raw_a)) else "")
                if body is auth_p.parent:
                    auth_p.decompose()
            if prof := box.select_one(".postprofile"):
                prof.decompose()
            items.append({"post_id": _norm_pid(box.get("id")), "author": author_txt, "post_date": date_txt, "body_tag": body})
        return "phpbb", items
    pb2, handled = [], []
    for tr in soup.find_all("tr"):
        if any(p in handled for p in tr.parents) or tr.find_parent(class_="quote") or (name_el := tr.find(class_="name")) is None or (pb_el := tr.find(class_="postbody")) is None or len(tr.find_all(class_="name")) > 1:
            continue
        handled.append(tr)
        author_txt, post_date = name_el.get_text(" ", strip=True), ""
        for pd in tr.find_all(class_="postdetails"):
            if m := re.search(r"Posted:\s*(.+?)(?:\s+Post subject:|$)", pd.get_text(" ", strip=True), re.I):
                post_date = m.group(1).strip()
            pd.decompose()
        outer_td = next((td for td in tr.find_all("td", recursive=False) if pb_el in td.descendants or pb_el is td), pb_el.find_parent("td"))
        anchor = tr.find("a", attrs={"name": True})
        pb2.append({"post_id": _norm_pid(anchor.get("name") if anchor else tr.get("id")), "author": author_txt, "post_date": post_date, "body_tag": pb_el if (outer_td is None or name_el in outer_td.descendants) else outer_td})
    return ("phpbb", pb2) if pb2 else None


def _extract_generic_posts(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract forum posts from generic <article>, .post blocks, or forum table rows."""
    items = []
    for node in [el for el in soup.select("article, .forum-post, div.post, li.post") if not el.find_parent("article")]:
        auth_el, time_el = node.select_one(".author, .username, .post-author, [rel='author'], [itemprop='author'], header strong, header b"), node.select_one("time, .date, .post-date, .timestamp")
        author_txt = auth_el.get_text(" ", strip=True) if auth_el else str(node.get("data-author", "")).strip()
        date_txt = time_el.get_text(" ", strip=True) if time_el else ""
        body = node.select_one(".post-content, .message, .entry-content, .body, .content, .posttext") or node
        if body is node:
            for meta in node.select("header, .posttop, .author, .username, .post-author, time, .date, .post-date, .post-meta"):
                meta.decompose()
        items.append({"post_id": _norm_pid(node.get("id") or node.get("data-post-id") or node.get("data-id")), "author": author_txt, "post_date": date_txt, "body_tag": body})
    if items:
        return items
    for tr in soup.find_all("tr"):
        if tr.find("th") or tr.find_parent(class_="quote") or len(tds := tr.find_all("td", recursive=False)) != 2:
            continue
        left, right, tbl = tds[0].get_text("\n", strip=True), tds[1].get_text(" ", strip=True), tr.find_parent("table")
        cls_blob = " ".join([*(tr.get("class") or []), *((tbl.get("class") or []) if tbl else []), str(tbl.get("id") or "") if tbl else ""]).lower()
        if (any(k in cls_blob for k in ("post", "message", "topic", "forum", "thread")) or re.search(r"\b(?:Joined:|Posts:|Originally Posted by)\b", f"{left} {right}", re.I)) and len(left) <= 80 and len(right) >= 20:
            dm = re.search(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{2,4})\b", left, re.I)
            items.append({"post_id": _norm_pid(tr.get("id")), "author": next((ln.strip() for ln in left.splitlines() if ln.strip()), ""), "post_date": dm.group(0) if dm else "", "body_tag": tds[1]})
    return items


def parse_forum_html(html: str, url: str = "", *, url_validator: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """Parse phpBB, vBulletin, or generic forum thread HTML into structured cleanroom posts."""
    soup = BeautifulSoup(html, "lxml")
    thread_title, thread_id, page_num = _extract_thread_metadata(soup, url)
    detected = _extract_phpbb_or_vb_posts(soup)
    engine, raw_posts = detected if detected is not None else ("generic", _extract_generic_posts(soup))
    posts = []
    for idx, raw in enumerate(raw_posts):
        body_text, quoted_authors, links = _clean_post_body(raw["body_tag"], url, url_validator=url_validator)
        author = enforce_heritage_safety(re.sub(r"\s+", " ", raw.get("author", "")).strip(), label="forum_author")
        clean_quoted = [enforce_heritage_safety(qa, label="forum_quoted_author") for qa in quoted_authors]
        post_date = enforce_heritage_safety(re.sub(r"\s+", " ", raw.get("post_date", "")).strip(), label="forum_post_date")
        posts.append({"author": author, "post_date": post_date, "post_id": str(raw.get("post_id", "")).strip(), "quoted_authors": clean_quoted, "body_text": enforce_heritage_safety(body_text, label=f"forum_post_{raw.get('post_id') or idx}"), "links": links, "is_op": page_num == 1 and idx == 0})
    return {"thread_title": enforce_heritage_safety(thread_title, label="forum_thread_title"), "thread_id": thread_id, "page_num": page_num, "url": url, "forum_engine": engine, "posts": posts}


def _format_thread_summary(title: str, posts: list[dict[str, Any]]) -> str:
    """Format a cleanroom Question / Topic -> Verified Resolution Markdown summary."""
    if not posts:
        return f"# {title or 'Forum Thread'}\n\n**Question / Topic -> Verified Resolution**\n\nNo posts extracted."
    op, replies, op_author = posts[0], posts[1:], posts[0].get("author") or "Unknown OP"
    res_posts, status = [], "Unconfirmed (single-post topic; verify against Tier 1 FSM)"
    if replies:
        op_confirms = [p for p in replies if p.get("author") == op_author and _RES_POS_RE.search(p.get("body_text", ""))]
        best = sorted(replies, key=lambda p: (bool(_RES_POS_RE.search(p.get("body_text", ""))), len(_TECH_DET_RE.findall(p.get("body_text", ""))), len(p.get("body_text", ""))), reverse=True)[0]
        if op_confirms:
            status, res_posts = "Confirmed by OP", ([best, op_confirms[-1]] if best is not op_confirms[-1] else [op_confirms[-1]])
        elif _RES_POS_RE.search(best.get("body_text", "")):
            status, res_posts = "Verified in thread replies", [best]
        else:
            status, res_posts = "Latest technical consensus (verify against Tier 1 FSM)", [best]
    lines = [f"# {title or 'Forum Thread'}", "", "**Question / Topic -> Verified Resolution**", "", "## Question / Topic", "", f"- **Author**: {op_author}", f"- **Date**: {op.get('post_date') or 'Unknown'}", "", op.get("body_text") or "_No body text._"]
    if replies:
        lines.extend(["", "## Discussion & Findings", ""])
        lines.extend(f"- **{r.get('author') or 'Contributor'}**: " + re.sub(r"\s+", " ", r.get("body_text", "")).strip() for r in replies)
    res_authors = ", ".join(dict.fromkeys((p.get("author") or "Contributor") for p in res_posts)) or "None"
    res_body = "\n\n".join(f"**{p.get('author') or 'Contributor'}**: {p.get('body_text', '')}" for p in res_posts if p.get("body_text")) or "Pending community resolution."
    lines.extend(["", "## Verified Resolution", "", f"- **Status**: {status}", f"- **Contributor(s)**: {res_authors}", "", res_body])
    return enforce_heritage_safety("\n".join(lines).strip(), label="forum_thread_summary")


def stitch_forum_threads(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group parsed forum pages by thread, order deterministically, deduplicate boundary posts, and summarize."""
    groups: dict[str, list[dict[str, Any]]] = {}
    title_to_key: dict[str, str] = {}
    for page in pages:
        raw_id, title_key = str(page.get("thread_id", "")).strip(), _canon_title(str(page.get("thread_title", ""))).lower()
        slug = re.sub(r"[^a-z0-9]+", "-", title_key).strip("-")[:64]
        gkey = f"id:{raw_id}" if (raw_id and raw_id != slug) else title_to_key.get(title_key, f"title:{title_key}" if title_key else f"id:{raw_id or len(groups)}")
        if title_key:
            title_to_key.setdefault(title_key, gkey)
        groups.setdefault(gkey, []).append(page)
    stitched: list[dict[str, Any]] = []
    for gpages in groups.values():
        ordered = sorted(gpages, key=lambda p: (int(p.get("page_num", 1) or 1), str(p.get("url", ""))))
        tid = next((str(p.get("thread_id", "")).strip() for p in ordered if p.get("thread_id")), "")
        raw_t = next((_canon_title(str(p.get("thread_title", ""))) for p in ordered if p.get("thread_title")), "")
        title = enforce_heritage_safety(raw_t or tid or "Forum Thread", label="stitched_thread_title")
        seen_ids, seen_sigs, seen_a, seen_qa, posts, authors, q_authors = set(), set(), set(), set(), [], [], []
        for page in ordered:
            for post in page.get("posts", []):
                pid = str(post.get("post_id", "")).strip()
                auth = enforce_heritage_safety(str(post.get("author", "")).strip(), label="stitched_author")
                post_date = enforce_heritage_safety(str(post.get("post_date", "")).strip(), label="stitched_post_date")
                body = enforce_heritage_safety(str(post.get("body_text", "")), label=f"stitched_post_{pid or len(posts)}")
                nb = re.sub(r"\s+", " ", body).strip()
                if (pid and pid in seen_ids) or (nb and (auth.lower(), nb) in seen_sigs and (not pid or len(nb) >= 40)):
                    continue
                if pid:
                    seen_ids.add(pid)
                if nb:
                    seen_sigs.add((auth.lower(), nb))
                q_list = [enforce_heritage_safety(str(q).strip(), label="stitched_quoted_author") for q in post.get("quoted_authors", []) if str(q).strip()]
                for qa in q_list:
                    _add_unique(q_authors, seen_qa, qa, casefold=True)
                _add_unique(authors, seen_a, auth, casefold=True)
                posts.append({**post, "author": auth, "post_date": post_date, "body_text": body, "quoted_authors": q_list, "is_op": len(posts) == 0})
        summary_md = _format_thread_summary(title, posts)
        stitched.append({"thread_id": tid, "thread_title": title, "page_count": len(ordered), "page_numbers": [int(p.get("page_num", 1) or 1) for p in ordered], "authors": authors, "quoted_authors": q_authors, "posts": posts, "summary_markdown": summary_md, "markdown": summary_md})
    return stitched


__all__ = ["HIJACKED_DOMAINS", "scrub_all_pii", "enforce_heritage_safety", "pre_ascii_table_to_gfm", "replace_pre_and_html_tables", "extract_clean_markdown", "extract_frameset_urls", "parse_forum_html", "stitch_forum_threads"]
