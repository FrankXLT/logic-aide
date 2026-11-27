"""Cleanroom two-pass 68HC11 cross-assembler backed by m68hc11 opcode tables."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import operator
from pathlib import Path
import re
import shutil
from typing import NamedTuple

import m68hc11

MNEMONIC_ALIASES: dict[str, str] = {
    "LSLD": "ASLD", "LSLA": "ASLA", "LSLB": "ASLB", "LSL": "ASL", "BHS": "BCC", "BLO": "BCS",
}
_META_DIRS = frozenset({".AREA", ".TITLE", ".DEFINE", ".MSG", ".LIST", ".NLIST", ".MODULE", ".GLOBL", ".END"})
_BYTE_DIRS, _WORD_DIRS = frozenset({".BYTE", ".DB", "FCB", "DB"}), frozenset({".WORD", ".DW", "FDB", "DW"})
_ASCII_DIRS, _RMB_DIRS = frozenset({".ASCII", ".STR", "FCC"}), frozenset({".RMB", ".DS", "RMB", ".BLKB"})
_ORG_DIRS, _INC_DIRS = frozenset({".ORG", "ORG"}), frozenset({".INCLUDE", "INCLUDE"})

_COL0_DATA_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_.]*)\s+((?:\.(?:byte|db|word|dw|rmb|ds|blkb|ascii|str)|(?:FCB|FDB|RMB|FCC|DB|DW))\b.*)$",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(r"^([A-Za-z_.][A-Za-z0-9_.]*):(?:\s*(.*))?$")
_EQUATE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*(?:==|=|\.(?:equ|EQU)\b|\b(?:EQU|equ)\b)\s*(.+)$")
_ASCII_LINE_RE = re.compile(r"^\s*(?:[A-Za-z_.][A-Za-z0-9_.]*:\s*)*(?:\.ascii|\.str|FCC)\s+(\S)", re.IGNORECASE)
_IDX_RE = re.compile(r"^(.*?),\s*([XY])\s*$", re.IGNORECASE)
_BIT_IDX_RE = re.compile(r"^\s*,\s*([XY])(?![A-Za-z0-9_.])", re.IGNORECASE)
_TOKEN_RE = re.compile(
    r"\s*(?:(?P<SH><<|>>)|(?P<OP>[+\-*/&|^~<>()])|(?P<PUNCT>[,#])|'(?P<CHR>(?:\\x[0-9A-Fa-f]{2}|\\.|[^'\\]))'"
    r"|\$%(?P<BIN1>[01]+)(?![0-9A-Za-z_])|%(?P<BIN2>[01]+)(?![0-9A-Za-z_])"
    r"|(?:\$|0[xXhH])(?P<HEX>[0-9A-Fa-f]+)(?![0-9A-Za-z_])|(?P<DEC>[0-9]+)(?![A-Za-z_])|(?P<IDENT>[A-Za-z_][A-Za-z0-9_.]*))"
)
_ESCAPES: dict[str, int] = {"n": 10, "r": 13, "t": 9, "\\": 92, "'": 39, '"': 34, "0": 0}
_PREC_LEVELS: tuple[tuple[str, ...], ...] = (("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/"))
_BIN_FNS = {"|": operator.or_, "^": operator.xor, "&": operator.and_, "+": operator.add, "-": operator.sub, "*": operator.mul}


class AssemblyError(ValueError):
    """Raised on 68HC11 syntax, addressing, bounds, overlap, or include errors."""


@dataclass(frozen=True)
class AssembledRom:
    """Immutable 68HC11 assembled ROM artifact and memory-map metadata."""

    rom_bytes: bytes
    base_address: int
    rom_size_bytes: int
    sha256: str
    symbols: dict[str, int]
    written_ranges: tuple[tuple[int, int], ...]
    table_ranges: tuple[tuple[int, int], ...]
    code_ranges: tuple[tuple[int, int], ...]
    source_files: tuple[str, ...]
    backend: str


def _build_opcode_index() -> dict[tuple[str, str, str | None], bytes]:
    index: dict[tuple[str, str, str | None], bytes] = {}
    for page, prefix, table in ((1, b"", m68hc11.PAGE1), (2, b"\x18", m68hc11.PAGE2), (3, b"\x1A", m68hc11.PAGE3), (4, b"\xCD", m68hc11.PAGE4)):
        for opcode, entry in table.items():
            idx_reg = ("Y" if page in (2, 4) else "X") if entry[1] in ("idx", "bidx", "bridx") else None
            index[(entry[0], entry[1], idx_reg)] = prefix + bytes((opcode,))
    return index


OPCODE_INDEX: dict[tuple[str, str, str | None], bytes] = _build_opcode_index()


def _decode_char(text: str, pos: int) -> tuple[int, int]:
    if text[pos] != "\\":
        return ord(text[pos]), pos + 1
    if pos + 1 >= len(text):
        raise AssemblyError("Trailing backslash in literal")
    esc = text[pos + 1]
    if esc in _ESCAPES:
        return _ESCAPES[esc], pos + 2
    if esc.lower() == "x" and pos + 3 < len(text):
        return int(text[pos + 2 : pos + 4], 16), pos + 4
    return ord(esc), pos + 2


def _tokenize_expr(text: str) -> list[tuple[str, int | str, int, int]]:
    tokens, pos, n = [], 0, len(text)
    while pos < n and not text[pos:].isspace():
        if (m := _TOKEN_RE.match(text, pos)) is None:
            raise AssemblyError(f"Invalid token near {text[pos:]!r}")
        pos, kind = m.end(), m.lastgroup
        if kind in ("SH", "OP", "PUNCT"):
            tokens.append(("PUNCT" if kind == "PUNCT" else "OP", m.group(kind), m.start(kind), pos))
        elif kind == "CHR":
            tokens.append(("INT", _decode_char(m.group("CHR"), 0)[0], m.start(), pos))
        elif kind in ("BIN1", "BIN2", "HEX", "DEC"):
            tokens.append(("INT", int(m.group(kind), 2 if kind.startswith("BIN") else (16 if kind == "HEX" else 10)), m.start(), pos))
        else:
            tokens.append(("IDENT", m.group("IDENT"), m.start("IDENT"), pos))
    return tokens


class _ExprParser:
    """Safe recursive-descent integer expression evaluator (no eval)."""

    def __init__(self, tokens: list[tuple[str, int | str, int, int]], symbols: dict[str, int], pc: int, *, allow_unknown: bool = False) -> None:
        self.tokens, self.symbols, self.pc, self.allow_unknown, self.pos, self.end_offset = tokens, symbols, pc, allow_unknown, 0, 0

    def _take_op(self, *ops: str) -> str | None:
        tok = self.tokens[self.pos] if self.pos < len(self.tokens) else None
        if tok and tok[0] == "OP" and tok[1] in ops:
            self.pos, self.end_offset = self.pos + 1, tok[3]
            return str(tok[1])
        return None

    def parse(self, level: int = 0) -> int:
        if level >= len(_PREC_LEVELS):
            return self._unary()
        val = self.parse(level + 1)
        while (op := self._take_op(*_PREC_LEVELS[level])) is not None:
            rhs = self.parse(level + 1)
            if op in ("<<", ">>"):
                if rhs < 0:
                    raise AssemblyError("Negative shift count")
                val = (val << rhs) if op == "<<" else (val >> rhs)
            elif op == "/":
                if rhs == 0 and not self.allow_unknown:
                    raise AssemblyError("Division by zero in expression")
                val = int(val // (rhs or 1))
            else:
                val = _BIN_FNS[op](val, rhs)
        return val

    def _unary(self) -> int:
        if (op := self._take_op("+", "-", "~", "<", ">")) is not None:
            v = self._unary()
            return v if op == "+" else (-v if op == "-" else (~v if op == "~" else ((v & 0xFF) if op == "<" else ((v >> 8) & 0xFF))))
        if self.pos >= len(self.tokens):
            raise AssemblyError("Unexpected end of expression")
        kind, val, _, end = self.tokens[self.pos]
        if kind == "INT" or (kind == "OP" and val == "*"):
            self.pos, self.end_offset = self.pos + 1, end
            return self.pc if kind == "OP" else int(val)
        if kind == "OP" and val == "(":
            self.pos += 1
            inner = self.parse(0)
            if not self._take_op(")"):
                raise AssemblyError("Unbalanced '(' in expression")
            return inner
        if kind == "IDENT":
            name, nxt = str(val), (self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None)
            if name.lower() in ("hi", "lo") and nxt and nxt[0] == "OP" and nxt[1] == "(":
                self.pos += 2
                inner = self.parse(0)
                if not self._take_op(")"):
                    raise AssemblyError(f"Unbalanced '(' in {name}()")
                return ((inner >> 8) & 0xFF) if name.lower() == "hi" else (inner & 0xFF)
            self.pos, self.end_offset = self.pos + 1, end
            if name in self.symbols or self.allow_unknown:
                return self.symbols.get(name, 1)
            raise AssemblyError(f"Undefined symbol: {name}")
        raise AssemblyError(f"Unexpected token {val!r} in expression")


def _eval_expr(text: str, symbols: dict[str, int], pc: int) -> int:
    if not (tokens := _tokenize_expr(text)):
        raise AssemblyError("Empty expression")
    p = _ExprParser(tokens, symbols, pc)
    val = p.parse()
    if p.pos != len(tokens):
        raise AssemblyError(f"Unexpected token {tokens[p.pos][1]!r} in expression {text!r}")
    return val


def _strip_comment(line: str) -> str:
    if line.lstrip().startswith("*"):
        return ""
    if (am := _ASCII_LINE_RE.match(line)) is not None:
        delim, j = am.group(1), am.start(1) + 1
        while j < len(line):
            if line[j] == "\\" and delim in ('"', "'") and j + 1 < len(line):
                j += 2
            elif line[j] == delim:
                return line[: line.find(";", j + 1)] if ";" in line[j + 1 :] else line
            else:
                j += 1
        return line
    in_q, i = None, 0
    while i < len(line):
        ch = line[i]
        if in_q and ch == "\\" and i + 1 < len(line):
            i += 2; continue
        in_q = (None if ch == in_q else in_q) if in_q else (ch if ch in ('"', "'") else None)
        if not in_q and ch == ";":
            return line[:i]
        i += 1
    return line


def _decode_fcc_bytes(arg: str) -> bytes:
    if not (s := arg.strip()):
        raise AssemblyError("Empty string literal directive")
    delim, out, i = s[0], bytearray(), 1
    while i < len(s):
        if s[i] == delim:
            if s[i + 1 :].strip():
                raise AssemblyError(f"Trailing text after string literal: {s[i + 1 :]!r}")
            return bytes(out)
        val, i = _decode_char(s, i) if (s[i] == "\\" and delim in ('"', "'")) else (ord(s[i]), i + 1)
        out.append(val & 0xFF)
    raise AssemblyError(f"Unterminated string literal: {arg!r}")


def _split_csv_items(arg: str) -> list[str]:
    items, cur, in_q, depth, i = [], [], None, 0, 0
    while i < len(arg):
        ch = arg[i]
        if in_q and ch == "\\" and i + 1 < len(arg):
            cur.extend((ch, arg[i + 1])); i += 2; continue
        in_q = (None if ch == in_q else in_q) if in_q else (ch if ch in ('"', "'") else None)
        depth += (1 if ch == "(" else (-1 if ch == ")" else 0)) if not in_q else 0
        if not in_q and ch == "," and depth == 0:
            if not (item := "".join(cur).strip()):
                raise AssemblyError(f"Empty item in list: {arg!r}")
            items.append(item); cur = []
        else:
            cur.append(ch)
        i += 1
    if not (last := "".join(cur).strip()):
        raise AssemblyError(f"Trailing comma or empty item in {arg!r}")
    return [*items, last]


def _resolve_include_path(raw_arg: str, current_dir: Path, base_dir: Path) -> Path:
    arg = raw_arg.strip()
    rel = arg[1:-1].strip() if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in ('"', "'", "/") else arg
    if not rel or any(ord(c) < 32 or ord(c) == 127 for c in rel) or rel.startswith(("/", "\\")) or ":" in rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise AssemblyError(f"Unsafe or invalid include path: {raw_arg!r}")
    candidate = (current_dir.resolve() / rel).resolve()
    try:
        candidate.relative_to(base_dir.resolve())
    except ValueError as exc:
        raise AssemblyError(f"Include path escapes base root: {raw_arg!r}") from exc
    if not candidate.is_file():
        raise AssemblyError(f"Included file not found: {rel}")
    return candidate


def _expand_lines(
    source: str, base_dir: Path | None, cur_dir: Path | None, label: str, stack: tuple[Path, ...], loaded: list[str]
) -> list[tuple[str, int, str, bool]]:
    if label not in loaded:
        loaded.append(label)
    records: list[tuple[str, int, str, bool]] = []
    for line_no, raw_line in enumerate(source.splitlines(), start=1):
        if not (stripped := _strip_comment(raw_line).strip()):
            continue
        parts = stripped.split(None, 1)
        if parts[0].upper() in _INC_DIRS:
            if len(parts) < 2 or base_dir is None or cur_dir is None:
                raise AssemblyError(f"{label}:{line_no}: Invalid .include or missing base_dir")
            inc = _resolve_include_path(parts[1], cur_dir, base_dir)
            if inc in stack:
                raise AssemblyError(f"Circular .include detected: {inc.name}")
            rel = inc.relative_to(base_dir.resolve()).as_posix()
            records.extend(_expand_lines(inc.read_text(encoding="utf-8"), base_dir, inc.parent, rel, (*stack, inc), loaded))
        else:
            records.append((label, line_no, stripped, bool(raw_line) and not raw_line[0].isspace()))
    return records


class _Stmt(NamedTuple):
    file: str; line_no: int; pc: int; kind: str; opcode: bytes = b""; mode: str = ""
    expr: str = ""; mask_expr: str = ""; target_expr: str = ""; items: tuple[str, ...] = (); raw_bytes: bytes = b""


def _split_expr_prefix(text: str) -> tuple[str, str]:
    if not (tokens := _tokenize_expr(text)):
        raise AssemblyError(f"Expected expression in {text!r}")
    p = _ExprParser(tokens, {}, 0, allow_unknown=True)
    p.parse()
    return text[: p.end_offset].strip(), text[p.end_offset :]


def _parse_bit_operand(operand: str, is_branch: bool) -> tuple[str | None, str, str, str | None]:
    if not (text := operand.strip()):
        raise AssemblyError("Missing operands for bit-manipulation instruction")
    forced_dir = text[0] in ("<", "*") and len(text) > 1 and not text[1].isspace() and text[1] not in "+-*/&|^<>"
    text = text[1:].lstrip() if forced_dir else text
    addr_expr, tail = ("0", text) if text.startswith(",") else _split_expr_prefix(text)
    idx_reg = None
    if not forced_dir and (m := _BIT_IDX_RE.match(tail)) is not None:
        idx_reg, tail = m.group(1).upper(), tail[m.end() :]
    mask_expr, tail = _split_expr_prefix(tail.lstrip().removeprefix(",").lstrip().removeprefix("#").lstrip())
    target_expr = None
    if is_branch:
        target_expr, tail = _split_expr_prefix(tail.lstrip().removeprefix(",").lstrip())
    if tail.strip():
        raise AssemblyError(f"Unexpected trailing operand text: {tail.strip()!r}")
    return idx_reg, addr_expr, mask_expr, target_expr


def _resolve_instruction(mnem: str, operand: str, symbols: dict[str, int], pc: int, file: str, line_no: int) -> tuple[_Stmt, int]:
    """Select exact opcode and byte length in Pass 1 so Pass 2 addresses never drift."""
    norm, op_str = MNEMONIC_ALIASES.get(mnem.upper(), mnem.upper()), operand.strip()
    if norm in ("BSET", "BCLR", "BRSET", "BRCLR"):
        is_br = norm in ("BRSET", "BRCLR")
        idx_reg, addr_e, mask_e, tgt_e = _parse_bit_operand(op_str, is_br)
        mode = ("bridx" if idx_reg else "brdir") if is_br else ("bidx" if idx_reg else "bdir")
        if (op := OPCODE_INDEX.get((norm, mode, idx_reg))) is None:
            raise AssemblyError(f"{file}:{line_no}: Unsupported mode {mode} for {norm}")
        return _Stmt(file, line_no, pc, "code", op, mode, addr_e, mask_e, tgt_e or ""), pc + len(op) + (3 if is_br else 2)
    if not op_str:
        if (op := OPCODE_INDEX.get((norm, "inh", None))) is None:
            raise AssemblyError(f"{file}:{line_no}: Unknown or non-inherent instruction {mnem!r}")
        return _Stmt(file, line_no, pc, "code", op, "inh"), pc + len(op)
    if op_str.startswith("#"):
        for mode in ("imm8", "imm16"):
            if (op := OPCODE_INDEX.get((norm, mode, None))) is not None:
                return _Stmt(file, line_no, pc, "code", op, mode, op_str[1:].strip()), pc + len(op) + (1 if mode == "imm8" else 2)
        raise AssemblyError(f"{file}:{line_no}: {norm} does not support immediate mode")
    if (idx_m := _IDX_RE.match(op_str)) is not None:
        idx_reg = idx_m.group(2).upper()
        if (op := OPCODE_INDEX.get((norm, "idx", idx_reg))) is None:
            raise AssemblyError(f"{file}:{line_no}: {norm} does not support ,{idx_reg} indexed mode")
        return _Stmt(file, line_no, pc, "code", op, "idx", idx_m.group(1).strip() or "0"), pc + len(op) + 1
    if (rel_op := OPCODE_INDEX.get((norm, "rel", None))) is not None:
        return _Stmt(file, line_no, pc, "code", rel_op, "rel", op_str), pc + len(rel_op) + 1
    forced = ("dir" if op_str[0] == "<" else "ext") if op_str[0] in ("<", ">") else (
        "dir" if op_str.startswith("*") and len(op_str) > 1 and not op_str[1].isspace() and op_str[1] not in "+-*/&|^<>" else None
    )
    expr = op_str[1:].strip() if forced else op_str
    if forced:
        if (op := OPCODE_INDEX.get((norm, forced, None))) is None:
            raise AssemblyError(f"{file}:{line_no}: {norm} does not support forced '{forced}' mode")
        return _Stmt(file, line_no, pc, "code", op, forced, expr), pc + len(op) + (1 if forced == "dir" else 2)
    if (dir_op := OPCODE_INDEX.get((norm, "dir", None))) is not None:
        try:
            if 0 <= _eval_expr(expr, symbols, pc) <= 0xFF:
                return _Stmt(file, line_no, pc, "code", dir_op, "dir", expr), pc + len(dir_op) + 1
        except AssemblyError:
            pass
    if (ext_op := OPCODE_INDEX.get((norm, "ext", None))) is not None:
        return _Stmt(file, line_no, pc, "code", ext_op, "ext", expr), pc + len(ext_op) + 2
    raise AssemblyError(f"{file}:{line_no}: Unknown instruction or invalid addressing mode: {mnem} {operand!r}")


def _def_sym(symbols: dict[str, int], name: str, val: int, file: str, line_no: int) -> None:
    if name in symbols:
        raise AssemblyError(f"{file}:{line_no}: Duplicate symbol {name!r}")
    symbols[name] = val


def _pass1(records: list[tuple[str, int, str, bool]], base_address: int) -> tuple[list[_Stmt], dict[str, int]]:
    stmts, symbols, deferred, pc = [], {}, [], base_address
    for file, line_no, text, col0 in records:
        cur = text
        while (lbl_m := _LABEL_RE.match(cur)) is not None:
            _def_sym(symbols, lbl_m.group(1), pc, file, line_no); cur = (lbl_m.group(2) or "").strip()
        if not cur:
            continue
        if (eq_m := _EQUATE_RE.match(cur)) is not None:
            sym, expr = eq_m.group(1), eq_m.group(2).strip()
            try:
                _def_sym(symbols, sym, _eval_expr(expr, symbols, pc), file, line_no)
            except AssemblyError:
                if sym in symbols:
                    raise
                deferred.append((sym, expr, pc, file, line_no))
            continue
        if col0 and cur == text and (col_m := _COL0_DATA_RE.match(cur)) is not None:
            _def_sym(symbols, col_m.group(1), pc, file, line_no); cur = col_m.group(2).strip()
        parts = cur.split(None, 1)
        head, rest, up = parts[0], (parts[1] if len(parts) > 1 else ""), parts[0].upper()
        if up in _META_DIRS:
            continue
        if up in _ORG_DIRS:
            if not (0 <= (pc := _eval_expr(rest, symbols, pc)) <= 0xFFFF):
                raise AssemblyError(f"{file}:{line_no}: .org address 0x{pc:X} out of 16-bit range")
        elif up in _RMB_DIRS:
            if (count := _eval_expr(rest, symbols, pc)) < 0:
                raise AssemblyError(f"{file}:{line_no}: Negative reservation count {count}")
            pc += count
        elif up in _BYTE_DIRS:
            for item in _split_csv_items(rest):
                raw = _decode_fcc_bytes(item) if (len(item) >= 2 and item[0] == '"' and item[-1] == '"') else None
                stmts.append(_Stmt(file, line_no, pc, "raw", raw_bytes=raw) if raw is not None else _Stmt(file, line_no, pc, "byte", items=(item,)))
                pc += len(raw) if raw is not None else 1
        elif up in _WORD_DIRS:
            items = tuple(_split_csv_items(rest))
            stmts.append(_Stmt(file, line_no, pc, "word", items=items)); pc += 2 * len(items)
        elif up in _ASCII_DIRS:
            raw = _decode_fcc_bytes(rest)
            stmts.append(_Stmt(file, line_no, pc, "raw", raw_bytes=raw)); pc += len(raw)
        else:
            stmt, pc = _resolve_instruction(head, rest, symbols, pc, file, line_no); stmts.append(stmt)
    while deferred:
        progressed, nxt = False, []
        for sym, expr, pc_val, file, line_no in deferred:
            try:
                _def_sym(symbols, sym, _eval_expr(expr, symbols, pc_val), file, line_no); progressed = True
            except AssemblyError:
                if sym in symbols:
                    raise
                nxt.append((sym, expr, pc_val, file, line_no))
        if not progressed:
            _eval_expr(nxt[0][1], symbols, nxt[0][2])
        deferred = nxt
    return stmts, symbols


def _chk(val: int, lo: int, hi: int, label: str, stmt: _Stmt) -> int:
    if not (lo <= val <= hi):
        raise AssemblyError(f"{stmt.file}:{stmt.line_no}: {label} value {val} out of range [{lo}, {hi}]")
    return val


def _emit_stmt_bytes(stmt: _Stmt, symbols: dict[str, int]) -> bytes:
    if stmt.kind == "raw":
        return stmt.raw_bytes
    if stmt.kind == "byte":
        return bytes((_chk(_eval_expr(stmt.items[0], symbols, stmt.pc), -128, 255, ".byte", stmt) & 0xFF,))
    if stmt.kind == "word":
        out = bytearray()
        for item in stmt.items:
            w = _chk(_eval_expr(item, symbols, stmt.pc), -32768, 65535, ".word", stmt) & 0xFFFF
            out.extend(((w >> 8) & 0xFF, w & 0xFF))
        return bytes(out)
    out, mode = bytearray(stmt.opcode), stmt.mode
    if mode in ("imm8", "dir", "idx"):
        out.append(_chk(_eval_expr(stmt.expr, symbols, stmt.pc), -128 if mode == "imm8" else 0, 255, mode, stmt) & 0xFF)
    elif mode in ("imm16", "ext"):
        w = _chk(_eval_expr(stmt.expr, symbols, stmt.pc), -32768 if mode == "imm16" else 0, 65535, mode, stmt) & 0xFFFF
        out.extend(((w >> 8) & 0xFF, w & 0xFF))
    elif mode == "rel":
        out.append(_chk(_eval_expr(stmt.expr, symbols, stmt.pc) - (stmt.pc + len(stmt.opcode) + 1), -128, 127, "branch", stmt) & 0xFF)
    elif mode in ("bdir", "bidx", "brdir", "bridx"):
        out.append(_chk(_eval_expr(stmt.expr, symbols, stmt.pc), 0, 255, mode, stmt))
        out.append(_chk(_eval_expr(stmt.mask_expr, symbols, stmt.pc), -128, 255, "mask", stmt) & 0xFF)
        if mode in ("brdir", "bridx"):
            out.append(_chk(_eval_expr(stmt.target_expr, symbols, stmt.pc) - (stmt.pc + len(stmt.opcode) + 3), -128, 127, "branch", stmt) & 0xFF)
    return bytes(out)


def _mask_to_ranges(mask: bytearray, base_address: int) -> tuple[tuple[int, int], ...]:
    ranges, start = [], None
    for idx, flag in enumerate(mask):
        if flag and start is None:
            start = base_address + idx
        elif not flag and start is not None:
            ranges.append((start, base_address + idx)); start = None
    return tuple(ranges if start is None else [*ranges, (start, base_address + len(mask))])


def _assemble_records(
    records: list[tuple[str, int, str, bool]], sources: tuple[str, ...], base_address: int, rom_size_bytes: int, fill_byte: int, backend: str
) -> AssembledRom:
    if rom_size_bytes <= 0 or base_address < 0 or base_address + rom_size_bytes > 0x10000 or not (0 <= fill_byte <= 0xFF):
        raise AssemblyError(f"Invalid ROM configuration base=0x{base_address:X}, size={rom_size_bytes}, fill={fill_byte}")
    stmts, symbols = _pass1(records, base_address)
    rom, written, table_mask, code_mask = bytearray([fill_byte]) * rom_size_bytes, bytearray(rom_size_bytes), bytearray(rom_size_bytes), bytearray(rom_size_bytes)
    for stmt in stmts:
        payload = _emit_stmt_bytes(stmt, symbols)
        if stmt.pc < base_address or stmt.pc + len(payload) > base_address + rom_size_bytes:
            raise AssemblyError(f"{stmt.file}:{stmt.line_no}: Emitted [0x{stmt.pc:04X}, 0x{stmt.pc + len(payload):04X}) outside ROM")
        off = stmt.pc - base_address
        for idx, bval in enumerate(payload):
            slot = off + idx
            if written[slot]:
                raise AssemblyError(f"{stmt.file}:{stmt.line_no}: Overlapping byte write at 0x{stmt.pc + idx:04X}")
            written[slot], rom[slot] = 1, bval
            (code_mask if stmt.kind == "code" else table_mask)[slot] = 1
    rom_bytes = bytes(rom)
    return AssembledRom(
        rom_bytes=rom_bytes, base_address=base_address, rom_size_bytes=rom_size_bytes, sha256=hashlib.sha256(rom_bytes).hexdigest(),
        symbols=symbols, written_ranges=_mask_to_ranges(written, base_address), table_ranges=_mask_to_ranges(table_mask, base_address),
        code_ranges=_mask_to_ranges(code_mask, base_address), source_files=sources, backend=backend,
    )


def detect_external_assembler() -> dict[str, str | None]:
    """Probe PATH for supported external 68HC11 cross-assemblers."""
    return {tool: shutil.which(tool) for tool in ("as11", "blackfoot", "a09")}


def assemble_source(
    source: str, *, base_address: int = 0x8000, rom_size_bytes: int = 32768, fill_byte: int = 0xFF, base_dir: Path | None = None
) -> AssembledRom:
    """Assemble cleanroom 68HC11 source text into an immutable ``AssembledRom``."""
    loaded: list[str] = []
    return _assemble_records(_expand_lines(source, base_dir, base_dir, "<source>", (), loaded), tuple(loaded), base_address, rom_size_bytes, fill_byte, "builtin")


def assemble_tree(
    entry_asm: Path | str, *, base_address: int = 0x8000, rom_size_bytes: int = 32768, fill_byte: int = 0xFF, backend: str = "builtin"
) -> AssembledRom:
    """Assemble an entry ``.asm`` file and its guarded ``.include`` tree."""
    entry_path = Path(entry_asm).resolve()
    if not entry_path.is_file():
        raise AssemblyError(f"Entry assembly file not found: {entry_asm}")
    if backend != "builtin":
        avail = detect_external_assembler()
        if backend not in avail or avail[backend] is None:
            raise AssemblyError(f"Assembler backend {backend!r} unavailable")
        raise AssemblyError(f"External assembler execution for {backend!r} is not configured; use backend='builtin'")
    base_dir, loaded = entry_path.parent, []
    records = _expand_lines(entry_path.read_text(encoding="utf-8"), base_dir, base_dir, entry_path.name, (entry_path,), loaded)
    return _assemble_records(records, tuple(loaded), base_address, rom_size_bytes, fill_byte, backend)


def format_s19(rom_bytes: bytes, base_address: int = 0x8000) -> str:
    """Serialize ``rom_bytes`` into Motorola S19 (S0/S1/S9) ASCII records."""
    if base_address < 0 or base_address + len(rom_bytes) > 0x10000:
        raise AssemblyError(f"S19 address range out of 16-bit bounds: 0x{base_address:X}+{len(rom_bytes)}")

    def _srec(rtype: str, addr: int, data: bytes) -> str:
        payload = bytes((len(data) + 3, (addr >> 8) & 0xFF, addr & 0xFF)) + data
        return f"{rtype}{payload.hex().upper()}{(~sum(payload)) & 0xFF:02X}"

    lines = [_srec("S0", 0x0000, b"logicaide")]
    lines.extend(_srec("S1", base_address + off, rom_bytes[off : off + 16]) for off in range(0, len(rom_bytes), 16))
    lines.append(_srec("S9", base_address, b""))
    return "\n".join(lines) + "\n"
