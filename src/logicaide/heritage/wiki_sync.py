"""Wiki validation and dual-publish mirror synchronization for LogicAIDE heritage docs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any

import yaml

from logicaide.heritage.mermaid_gate import MermaidGateError, check_mermaid_syntax_strict

CHROME_PARTIALS = frozenset({"_Sidebar.md", "_Footer.md"})
VALID_TIERS = frozenset({"Tier 1", "Tier 2", "Tier 3"})
VALID_STATUSES = frozenset({"verified", "needs_human_review", "stub"})
DEFAULT_STAGE_DIR = Path("data/heritage_raw/wiki_stage")
DEFAULT_GITHUB_WIKI_STAGE_DIR = Path("data/heritage_raw/wiki_github_mirror")
REQUIRED_FRONTMATTER_FIELDS = (
    "title", "slug", "category", "tier", "provenance",
    "ecu_scope", "engine_scope", "status", "last_verified", "tags",
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"^([`~]{3,})(.*)$")
_INLINE_CODE_RE = re.compile(r"`+[^`]*`+")
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
_EXTERNAL_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Windows user path", re.compile(r"C:\\Users\\", re.IGNORECASE)),
    ("POSIX home path", re.compile(r"/home/")),
    ("ECU Flash tool token", re.compile(r"Flash\.frm", re.IGNORECASE)),
    ("ECU write command byte", re.compile(r"\b0x(?:41|51)\b", re.IGNORECASE)),
    ("Serial device path", re.compile(r"/dev/ttyUSB\d*", re.IGNORECASE)),
    ("Email address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
)


class WikiValidationError(ValueError):
    """Raised when wiki frontmatter, links, Mermaid blocks, or PII checks fail."""


@dataclass(frozen=True)
class _WikiPage:
    """Parsed metadata and raw text for a single markdown file under wiki/."""

    abs_path: Path
    rel_path: Path
    raw_text: str
    body_text: str
    is_chrome: bool
    frontmatter: dict[str, Any] | None


def _is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, text
    return match.group(1), text[match.end() :]


def _validate_frontmatter(rel_path: Path, raw_yaml: str | None) -> tuple[dict[str, Any], list[str]]:
    """Parse and validate required content-page YAML frontmatter."""
    prefix = rel_path.as_posix()
    if raw_yaml is None:
        raise WikiValidationError(f"{prefix}: missing YAML frontmatter block")
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise WikiValidationError(f"{prefix}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(parsed, dict):
        raise WikiValidationError(f"{prefix}: YAML frontmatter must be a mapping")

    missing = [key for key in REQUIRED_FRONTMATTER_FIELDS if key not in parsed]
    if missing:
        raise WikiValidationError(f"{prefix}: missing frontmatter fields: {', '.join(missing)}")

    for str_field in ("title", "slug", "category", "provenance"):
        if not _is_non_empty_str(parsed[str_field]):
            raise WikiValidationError(f"{prefix}: '{str_field}' must be a non-empty string")
    if parsed["tier"] not in VALID_TIERS:
        raise WikiValidationError(f"{prefix}: 'tier' must be one of {sorted(VALID_TIERS)}")
    if parsed["status"] not in VALID_STATUSES:
        raise WikiValidationError(f"{prefix}: 'status' must be one of {sorted(VALID_STATUSES)}")

    last_verified = parsed["last_verified"]
    if not (isinstance(last_verified, (date, datetime)) or _is_non_empty_str(last_verified)):
        raise WikiValidationError(f"{prefix}: 'last_verified' must be an ISO date or string")

    for list_field in ("ecu_scope", "engine_scope", "tags"):
        val = parsed[list_field]
        if not (isinstance(val, list) and all(_is_non_empty_str(x) for x in val)):
            raise WikiValidationError(f"{prefix}: '{list_field}' must be a list of non-empty strings")

    warnings = [f"{prefix}: status is '{parsed['status']}'"] if parsed["status"] != "verified" else []
    return parsed, warnings


def _scan_forbidden_tokens(rel_path: Path, text: str) -> list[str]:
    """Return error strings for any forbidden PII, path, or Flash tokens."""
    errors: list[str] = []
    for label, pattern in _FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        if match:
            errors.append(f"{rel_path.as_posix()}: forbidden {label} detected ({match.group(0)!r})")
    return errors


def _extract_mermaid_blocks(rel_path: Path, text: str) -> list[str]:
    """Extract all mermaid fenced blocks while validating fence closure."""
    blocks, current = [], []
    in_fence, fence_char, fence_len, is_mermaid = False, "", 0, False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_fence:
            if match := _FENCE_OPEN_RE.match(stripped):
                marker, info = match.group(1), match.group(2).strip().lower()
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
                is_mermaid, current = (info.split()[0] == "mermaid" if info else False), []
        elif re.match(rf"^{re.escape(fence_char)}{{{fence_len},}}\s*$", stripped):
            if is_mermaid:
                blocks.append("\n".join(current))
            in_fence, is_mermaid, current = False, False, []
        elif is_mermaid:
            current.append(line)
    if in_fence and is_mermaid:
        raise WikiValidationError(f"{rel_path.as_posix()}: unclosed mermaid fenced code block")
    return blocks


def validate_mermaid_block(block: str, use_external_linter: bool = False) -> None:
    """Validate Mermaid block structure deterministically and optionally run an external linter."""
    try:
        check_mermaid_syntax_strict(block, use_external_linter=use_external_linter)
    except MermaidGateError as exc:
        raise WikiValidationError(str(exc)) from exc


def _parse_link_target(raw_target: str) -> tuple[str, str]:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")].strip()
    elif title_match := re.match(r'^(.+?)\s+(?:"[^"]*"|\'[^\']*\')\s*$', target):
        target = title_match.group(1).strip()
    if "#" in target:
        path_part, anchor = target.split("#", 1)
        return path_part.strip(), f"#{anchor}"
    return target, ""


def _resolve_wiki_target(
    source_abs: Path,
    wiki_root: Path,
    raw_target: str,
    flat_name_index: dict[str, list[Path]],
    slug_index: dict[str, list[Path]],
) -> Path | None:
    """Resolve a relative or GitHub Wiki flat link inside wiki_root, or return None."""
    path_part, anchor = _parse_link_target(raw_target)
    if not path_part:
        return source_abs if anchor else None
    if _EXTERNAL_URL_RE.match(path_part):
        return source_abs

    candidates: list[Path] = []
    if path_part.startswith("/"):
        rel = path_part.lstrip("/")
        bases = [wiki_root / rel]
    else:
        bases = [source_abs.parent / path_part]
        if "/" not in path_part and "\\" not in path_part:
            bases.append(wiki_root / path_part)
    for base in bases:
        candidates.append(base.resolve())
        if "." not in base.name:
            candidates.append(Path(f"{base}.md").resolve())

    for candidate in candidates:
        if candidate.is_relative_to(wiki_root) and candidate.is_file():
            return candidate

    if "/" not in path_part and "\\" not in path_part and not path_part.startswith("."):
        stem = path_part[:-3] if path_part.lower().endswith(".md") else path_part
        for key_map in (flat_name_index, slug_index):
            if len(matches := key_map.get(stem, [])) == 1:
                return matches[0]
    return None


def _iter_code_aware_lines(text: str):
    """Yield (line, is_code_line) tracking fenced code blocks."""
    in_fence, fence_char, fence_len = False, "", 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not in_fence:
            if match := _FENCE_OPEN_RE.match(stripped):
                marker = match.group(1)
                in_fence, fence_char, fence_len = True, marker[0], len(marker)
                yield line, True
                continue
            yield line, False
        else:
            if re.match(rf"^{re.escape(fence_char)}{{{fence_len},}}\s*$", stripped):
                in_fence = False
            yield line, True


def _validate_page_links(
    page: _WikiPage, wiki_root: Path, flat_name_index: dict[str, list[Path]], slug_index: dict[str, list[Path]]
) -> list[str]:
    """Return validation error messages for broken/escaping relative links."""
    errors: list[str] = []
    for line, is_code in _iter_code_aware_lines(page.raw_text):
        if is_code:
            continue
        for match in _MARKDOWN_LINK_RE.finditer(_INLINE_CODE_RE.sub("", line)):
            raw_target = match.group(2).strip()
            path_part, anchor = _parse_link_target(raw_target)
            if (not path_part and anchor) or _EXTERNAL_URL_RE.match(path_part):
                continue
            if _resolve_wiki_target(page.abs_path, wiki_root, raw_target, flat_name_index, slug_index) is None:
                errors.append(f"{page.rel_path.as_posix()}: broken or out-of-tree link {raw_target!r}")
    return errors


def _has_symlink_under(root: Path, path: Path) -> bool:
    """Return True if path or any component strictly below root is a symlink."""
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    cur = root
    for part in rel_parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def _load_and_validate_tree(
    wiki_dir: str | Path, use_external_linter: bool = False
) -> tuple[Path, list[_WikiPage], dict[str, list[Path]], dict[str, list[Path]], dict[str, Any]]:
    """Validate all markdown files under wiki_dir and return parsed pages + indices."""
    wiki_root = Path(wiki_dir).resolve()
    if not wiki_root.is_dir():
        raise WikiValidationError(f"{wiki_dir}: wiki directory does not exist")
    md_files: list[Path] = []
    for p in sorted(wiki_root.rglob("*.md")):
        if p.is_symlink() or _has_symlink_under(wiki_root, p):
            raise WikiValidationError(f"{p.name}: symlink or out-of-tree markdown file is not allowed")
        try:
            resolved_p = p.resolve()
        except OSError as exc:
            raise WikiValidationError(f"{p.name}: symlink or out-of-tree markdown file is not allowed") from exc
        if not resolved_p.is_relative_to(wiki_root):
            raise WikiValidationError(f"{p.name}: symlink or out-of-tree markdown file is not allowed")
        if p.is_file():
            md_files.append(resolved_p)
    if not md_files:
        raise WikiValidationError(f"{wiki_dir}: no markdown files found")

    errors, warnings, pages = [], [], []
    flat_name_index: dict[str, list[Path]] = {}
    slug_index: dict[str, list[Path]] = {}
    mermaid_count = 0

    for abs_path in md_files:
        try:
            rel_path = abs_path.relative_to(wiki_root)
        except ValueError as exc:
            raise WikiValidationError(f"{abs_path.name}: symlink or out-of-tree markdown file is not allowed") from exc
        raw_text = abs_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        errors.extend(_scan_forbidden_tokens(rel_path, raw_text))
        is_chrome = abs_path.name in CHROME_PARTIALS
        raw_yaml, body_text = _split_frontmatter(raw_text)
        frontmatter: dict[str, Any] | None = None
        if not is_chrome:
            try:
                frontmatter, page_warnings = _validate_frontmatter(rel_path, raw_yaml)
                warnings.extend(page_warnings)
            except WikiValidationError as exc:
                errors.append(str(exc))

        try:
            blocks = _extract_mermaid_blocks(rel_path, raw_text)
        except WikiValidationError as exc:
            errors.append(str(exc))
            blocks = []
        for idx, block in enumerate(blocks, start=1):
            mermaid_count += 1
            try:
                validate_mermaid_block(block, use_external_linter=use_external_linter)
            except WikiValidationError as exc:
                errors.append(f"{rel_path.as_posix()} (mermaid #{idx}): {exc}")

        if not is_chrome:
            flat_name_index.setdefault(abs_path.stem, []).append(abs_path)
            if frontmatter and isinstance(frontmatter.get("slug"), str):
                slug_index.setdefault(frontmatter["slug"], []).append(abs_path)
        pages.append(_WikiPage(abs_path, rel_path, raw_text, body_text, is_chrome, frontmatter))

    for page in pages:
        errors.extend(_validate_page_links(page, wiki_root, flat_name_index, slug_index))
    if errors:
        raise WikiValidationError("\n".join(errors))

    summary = {"validated_files": len(pages), "mermaid_blocks_checked": mermaid_count, "warnings": warnings}
    return wiki_root, pages, flat_name_index, slug_index, summary


def validate_wiki_tree(wiki_dir: str | Path, *, use_external_linter: bool = False) -> dict[str, Any]:
    """Validate frontmatter, links, Mermaid diagrams, and PII rules across wiki_dir."""
    _, _, _, _, summary = _load_and_validate_tree(wiki_dir, use_external_linter=use_external_linter)
    return summary


def _rewrite_links_for_flat_wiki(
    text: str, page: _WikiPage, wiki_root: Path, flat_name_index: dict[str, list[Path]], slug_index: dict[str, list[Path]]
) -> str:
    """Rewrite relative directory links to flattened filenames outside code spans/fences."""
    def _replace_match(match: re.Match[str]) -> str:
        label, raw_target = match.group(1), match.group(2)
        path_part, anchor = _parse_link_target(raw_target)
        if not path_part or _EXTERNAL_URL_RE.match(path_part):
            return match.group(0)
        resolved = _resolve_wiki_target(page.abs_path, wiki_root, raw_target, flat_name_index, slug_index)
        if resolved is None or resolved.suffix.lower() != ".md":
            return match.group(0)
        new_path = resolved.name if path_part.lower().endswith(".md") else resolved.stem
        return f"[{label}]({new_path}{anchor})"

    out_lines: list[str] = []
    for line, is_code in _iter_code_aware_lines(text):
        if is_code:
            out_lines.append(line)
            continue
        parts, last = [], 0
        for code_match in _INLINE_CODE_RE.finditer(line):
            parts.append(_MARKDOWN_LINK_RE.sub(_replace_match, line[last : code_match.start()]))
            parts.append(code_match.group(0))
            last = code_match.end()
        parts.append(_MARKDOWN_LINK_RE.sub(_replace_match, line[last:]))
        out_lines.append("".join(parts))
    return "".join(out_lines)


def sync_wiki(
    wiki_dir: str | Path, target_dir: str | Path | None = None, dry_run: bool = False,
    *, strip_frontmatter: bool = True, flatten_for_github_wiki: bool = True,
    use_external_linter: bool = False,
) -> dict[str, Any]:
    """Validate wiki_dir and mirror pages into target_dir without mutating wiki_dir."""
    wiki_root, pages, flat_name_index, slug_index, summary = _load_and_validate_tree(
        wiki_dir, use_external_linter=use_external_linter
    )
    target_path = Path(target_dir) if target_dir is not None else DEFAULT_STAGE_DIR
    if target_path.is_symlink():
        raise WikiValidationError(f"Target wiki directory must not be a symlink: {target_path}")
    resolved_target = target_path.resolve()
    if resolved_target == wiki_root or resolved_target.is_relative_to(wiki_root) or wiki_root.is_relative_to(resolved_target):
        raise WikiValidationError(f"Refusing to sync into source wiki directory: {resolved_target}")

    staged_outputs: list[tuple[Path, str]] = []
    seen_targets: dict[Path, Path] = {}
    for page in pages:
        dest_rel = Path(page.abs_path.name) if flatten_for_github_wiki else page.rel_path
        if dest_rel in seen_targets:
            raise WikiValidationError(
                f"Flattened filename collision for {dest_rel.as_posix()}: "
                f"{seen_targets[dest_rel].as_posix()} vs {page.rel_path.as_posix()}"
            )
        seen_targets[dest_rel] = page.rel_path
        content = page.body_text.lstrip("\n") if (strip_frontmatter and not page.is_chrome) else page.raw_text
        if flatten_for_github_wiki:
            content = _rewrite_links_for_flat_wiki(content, page, wiki_root, flat_name_index, slug_index)
        if content and not content.endswith("\n"):
            content += "\n"
        staged_outputs.append((resolved_target / dest_rel, content))

    synced_count = 0
    if not dry_run:
        for dest_path, content in staged_outputs:
            try:
                resolved_dest = dest_path.resolve(strict=False)
            except OSError as exc:
                raise WikiValidationError(
                    f"Refusing to write to symlink or out-of-tree destination: {dest_path}"
                ) from exc
            if (
                dest_path.is_symlink()
                or _has_symlink_under(resolved_target, dest_path)
                or not resolved_dest.is_relative_to(resolved_target)
            ):
                raise WikiValidationError(
                    f"Refusing to write to symlink or out-of-tree destination: {dest_path}"
                )
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(content, encoding="utf-8", newline="\n")
            synced_count += 1

    return {
        **summary, "synced_files": synced_count, "dry_run": dry_run, "target_dir": str(resolved_target),
    }


def run_sync_wiki(
    wiki_dir: str | Path, target_dir: str | Path | None = None, dry_run: bool = False,
    validate_only: bool = False, *, strip_frontmatter: bool = True,
    flatten_for_github_wiki: bool = True, use_external_linter: bool = False,
    push_github_wiki: bool = False, wiki_remote: str | None = None,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run wiki validation and optional dual-publish or GitHub .wiki.git mirror sync."""
    if validate_only and (push_github_wiki or wiki_remote is not None):
        raise WikiValidationError("validate_only cannot be combined with push_github_wiki or wiki_remote")
    if wiki_remote is not None and not push_github_wiki:
        raise WikiValidationError("--wiki-remote requires push_github_wiki=True.")
    if validate_only:
        summary = validate_wiki_tree(wiki_dir, use_external_linter=use_external_linter)
        return {**summary, "synced_files": 0, "dry_run": dry_run, "validate_only": True}
    if push_github_wiki:
        from logicaide.heritage.github_wiki import push_github_wiki_mirror

        return push_github_wiki_mirror(
            wiki_dir=wiki_dir, target_dir=target_dir, dry_run=dry_run,
            strip_frontmatter=strip_frontmatter, flatten_for_github_wiki=flatten_for_github_wiki,
            use_external_linter=use_external_linter, wiki_remote=wiki_remote, repo_root=repo_root,
        )
    result = sync_wiki(
        wiki_dir=wiki_dir, target_dir=target_dir, dry_run=dry_run, strip_frontmatter=strip_frontmatter,
        flatten_for_github_wiki=flatten_for_github_wiki, use_external_linter=use_external_linter,
    )
    result["validate_only"] = False
    return result
