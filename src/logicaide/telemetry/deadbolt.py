"""Hardware Deadbolt and manifest-only SCI TX gate enforcing SECURITY.md.

This module guarantees automated telemetry code can only open in-memory test
transports (``loop://`` or ``memory://``) and may only transmit read-only SCI
commands verified in ``rom_manifest.json``.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


class HardwareDeadboltError(ValueError):
    """Raised when an unsafe serial target or forbidden SCI TX payload is blocked."""


class NoVerifiedSourceMatchError(HardwareDeadboltError):
    """Raised when an SCI table/offset lacks source_verified=True in the manifest."""


ALLOWED_SINGLE_BYTE_TX: frozenset[int] = frozenset({0x12, 0xFE})

FORBIDDEN_TX_OPCODES: dict[int, str] = {
    0x13: "Actuator Test Mode (ATM)",
    0x17: "Clear Stored DTCs",
    0x23: "Erase/Clear Memory",
    0x41: "ECU Write/Flash",
    0x51: "ECU Write/Flash",
}

SCI_TABLE_MIN: int = 0xF0
SCI_TABLE_MAX: int = 0xFD

_POSIX_SERIAL_DEVICE_RE = re.compile(r"^/dev/(?:tty[^/]*|cu\.[^/]+)$", re.IGNORECASE)
_WINDOWS_COM_PORT_RE = re.compile(r"^(?:\\\\\.\\)?COM\d+$", re.IGNORECASE)
_REMOTE_DISCOVERY_SCHEME_RE = re.compile(
    r"^(?:socket|rfc2217|hwgrep|alt|spy)://",
    re.IGNORECASE,
)
_ALLOWED_TEST_SCHEME_RE = re.compile(r"^(?:loop|memory)://", re.IGNORECASE)


def _has_control_or_whitespace(text: str) -> bool:
    return any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text)


def validate_serial_target(port: str) -> str:
    """Validate that a serial target is an offline test URL per SECURITY.md.

    Only ``loop://`` and ``memory://`` test transports are permitted. Physical
    POSIX ``/dev/`` serial devices, Windows ``COM`` ports, and remote/discovery
    URL schemes are unconditionally rejected with ``HardwareDeadboltError``.
    """
    if not isinstance(port, str) or not port:
        raise HardwareDeadboltError(
            "Empty or non-string serial port rejected by SECURITY.md Hardware Deadbolt."
        )
    if _has_control_or_whitespace(port):
        raise HardwareDeadboltError(
            f"Serial target {port!r} contains whitespace or control characters (SECURITY.md)."
        )
    if _POSIX_SERIAL_DEVICE_RE.match(port) or port.startswith("/dev/"):
        raise HardwareDeadboltError(
            f"Physical POSIX serial device {port!r} is strictly prohibited by SECURITY.md."
        )
    if _WINDOWS_COM_PORT_RE.match(port):
        raise HardwareDeadboltError(
            f"Physical Windows COM port {port!r} is strictly prohibited by SECURITY.md."
        )
    if _REMOTE_DISCOVERY_SCHEME_RE.match(port):
        raise HardwareDeadboltError(
            f"Remote or hardware-discovery serial URL {port!r} is prohibited by SECURITY.md."
        )
    if not _ALLOWED_TEST_SCHEME_RE.match(port):
        raise HardwareDeadboltError(
            f"Unsupported serial target {port!r}: SECURITY.md permits only loop:// or memory:// test transports."
        )
    return port


def _parse_sci_table_code(raw: Any) -> int | None:
    """Parse a 0xF0-0xFD SCI table identifier from int or hex string."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if SCI_TABLE_MIN <= raw <= SCI_TABLE_MAX else None
    if isinstance(raw, str):
        token = raw.strip()
        if token.lower().startswith("0x"):
            try:
                value = int(token, 16)
            except ValueError:
                return None
            return value if SCI_TABLE_MIN <= value <= SCI_TABLE_MAX else None
    return None


def _parse_offset_byte(entry: Mapping[str, Any]) -> int | None:
    """Extract an 8-bit RAM offset (0x00-0xFF) from a watchlist entry."""
    raw_offset = entry.get("offset")
    if isinstance(raw_offset, int) and not isinstance(raw_offset, bool):
        return raw_offset if 0 <= raw_offset <= 0xFF else None
    raw_hex = entry.get("offset_hex")
    if isinstance(raw_hex, str) and raw_hex.strip().lower().startswith("0x"):
        try:
            value = int(raw_hex.strip(), 16)
        except ValueError:
            return None
        return value if 0 <= value <= 0xFF else None
    return None


class SciTxGate:
    """Manifest-backed gate allowing only read-only, source-verified SCI TX frames."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        """Index verified 0xF0-0xFD watchlist entries declared in ``manifest``."""
        if not isinstance(manifest, Mapping):
            raise HardwareDeadboltError("SCI manifest must be a mapping (SECURITY.md).")

        allowed_tables: set[int] = set()
        for raw_table in manifest.get("sci_tables", ()) or ():
            code = _parse_sci_table_code(raw_table)
            if code is not None:
                allowed_tables.add(code)
        self._allowed_tables: frozenset[int] = frozenset(allowed_tables)

        verified: dict[tuple[int, int], dict[str, Any]] = {}
        for entry in manifest.get("ram_watchlist", ()) or ():
            if not isinstance(entry, Mapping):
                continue
            if entry.get("source_verified") is not True:
                continue
            fx = _parse_sci_table_code(entry.get("sci_table"))
            if fx is None or fx not in self._allowed_tables:
                continue
            offset = _parse_offset_byte(entry)
            if offset is None:
                continue
            verified.setdefault((fx, offset), dict(entry))
        self._verified_entries: dict[tuple[int, int], dict[str, Any]] = verified

    @property
    def allowed_tables(self) -> frozenset[int]:
        """Return the set of manifest-enabled 0xF0-0xFD SCI table codes."""
        return self._allowed_tables

    def is_verified_read(self, fx: int, offset: int) -> bool:
        """Return True iff ``(fx, offset)`` is source-verified in the manifest."""
        if isinstance(fx, bool) or isinstance(offset, bool):
            return False
        if not isinstance(fx, int) or not isinstance(offset, int):
            return False
        return (fx, offset) in self._verified_entries

    def get_verified_entry(self, fx: int, offset: int) -> dict[str, Any]:
        """Return the verified watchlist entry for ``(fx, offset)`` or raise."""
        if isinstance(fx, bool) or isinstance(offset, bool) or not isinstance(fx, int) or not isinstance(offset, int):
            raise NoVerifiedSourceMatchError(
                f"No Verified Source Match: invalid SCI key ({fx!r}, {offset!r})."
            )
        entry = self._verified_entries.get((fx, offset))
        if entry is None:
            raise NoVerifiedSourceMatchError(
                f"No Verified Source Match for SCI table 0x{fx:02X} offset 0x{offset:02X} "
                "in rom_manifest.json (SECURITY.md)."
            )
        return dict(entry)

    def assert_tx_allowed(self, payload: bytes | bytearray) -> None:
        """Allow only 1-byte (0x12, 0xFE) or verified 2-byte [FX, AA] read frames."""
        if not isinstance(payload, (bytes, bytearray)):
            raise HardwareDeadboltError(
                f"SCI TX payload must be bytes or bytearray, got {type(payload).__name__}."
            )
        data = bytes(payload)
        if not data:
            raise HardwareDeadboltError("Empty SCI TX payload is forbidden by SECURITY.md.")

        opcode = data[0]
        if opcode in FORBIDDEN_TX_OPCODES:
            raise HardwareDeadboltError(
                f"Forbidden SCI TX opcode 0x{opcode:02X} ({FORBIDDEN_TX_OPCODES[opcode]}) "
                "is hard-blocked by SECURITY.md Hardware Deadbolt."
            )
        if len(data) > 2:
            raise HardwareDeadboltError(
                f"Multi-byte SCI TX sequence ({len(data)} bytes) is blocked by SECURITY.md "
                "to prevent bootstrap loaders or ECU writes."
            )
        if len(data) == 1:
            if opcode in ALLOWED_SINGLE_BYTE_TX:
                return
            raise HardwareDeadboltError(
                f"Single-byte SCI TX opcode 0x{opcode:02X} is not in ALLOWED_SINGLE_BYTE_TX (SECURITY.md)."
            )

        fx, offset = data[0], data[1]
        if not (SCI_TABLE_MIN <= fx <= SCI_TABLE_MAX):
            raise HardwareDeadboltError(
                f"2-byte SCI TX table 0x{fx:02X} is outside 0xF0-0xFD read tables (SECURITY.md)."
            )
        if fx not in self._allowed_tables:
            raise NoVerifiedSourceMatchError(
                f"No Verified Source Match: SCI table 0x{fx:02X} is not declared in manifest sci_tables."
            )
        self.get_verified_entry(fx, offset)


def open_read_only_serial(
    port: str,
    *,
    baudrate: int = 7812,
    timeout: float = 0.012,
) -> Any:
    """Validate ``port`` against Hardware Deadbolt and open via ``serial_for_url``."""
    target = validate_serial_target(port)
    if baudrate <= 0:
        raise ValueError(f"baudrate must be positive, got {baudrate!r}")
    if timeout < 0:
        raise ValueError(f"timeout must be non-negative, got {timeout!r}")
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "pyserial is required to open loop:// or memory:// test serial transports (install pyserial>=3.5)."
        ) from exc
    return serial.serial_for_url(target, baudrate=baudrate, timeout=timeout)


__all__ = [
    "ALLOWED_SINGLE_BYTE_TX",
    "FORBIDDEN_TX_OPCODES",
    "HardwareDeadboltError",
    "NoVerifiedSourceMatchError",
    "SCI_TABLE_MAX",
    "SCI_TABLE_MIN",
    "SciTxGate",
    "open_read_only_serial",
    "validate_serial_target",
]
