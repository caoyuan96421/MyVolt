"""Protocol helpers for the Voltera-like MyVolt controller."""

from __future__ import annotations

from dataclasses import dataclass
import re


FIXED_BAUD_RATE = 250000
LINE_TERMINATOR = b"\n"


def crc8_maxim(data: bytes) -> int:
    """Return CRC-8/MAXIM-Dallas for *data*."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = ((crc >> 1) ^ 0x8C) & 0xFF
            else:
                crc = (crc >> 1) & 0xFF
    return crc


def frame_command(sequence: int, payload: str) -> str:
    """Frame a payload as N-prefixed controller command."""
    payload = payload.strip()
    prefix = f"N{sequence} {payload} "
    checksum = crc8_maxim(prefix.encode("ascii"))
    body = f"{prefix}*{checksum:02x}"
    return f"{body},{len(body)}"


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float
    e: float


@dataclass(frozen=True)
class Temperature:
    current: float
    target: float
    time_remaining: float


@dataclass(frozen=True)
class ProbeMeasurement:
    x: float
    y: float
    z: float
    displacement: float
    samples_taken: int | None
    touches_used: int | None
    point_id: str


@dataclass(frozen=True)
class HomedStatus:
    x: int
    y: int
    z: int

    @property
    def unhomed_axes(self) -> tuple[str, ...]:
        axes: list[str] = []
        if self.x != -1:
            axes.append("X")
        if self.y != -1:
            axes.append("Y")
        if self.z != -1:
            axes.append("Z")
        return tuple(axes)

    @property
    def all_homed(self) -> bool:
        return not self.unhomed_axes


@dataclass(frozen=True)
class ToolStatus:
    tool_type: str
    version: int | None


_FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"

_POSITION_RE = re.compile(
    rf"positionUpdate\s+x:(?P<x>{_FLOAT})\s+y:(?P<y>{_FLOAT})\s+"
    rf"z:(?P<z>{_FLOAT})\s+e:(?P<e>{_FLOAT})"
)

_TEMPERATURE_RE = re.compile(
    rf"bedTemperatureUpdate\s+current:(?P<current>{_FLOAT})\s+"
    rf"target:(?P<target>{_FLOAT})\s+timeRemaining:(?P<remaining>{_FLOAT})"
)

_PROBE_RE = re.compile(
    rf"probeMeasurement\s+x:(?P<x>{_FLOAT})\s+y:(?P<y>{_FLOAT})\s+"
    rf"z:(?P<z>{_FLOAT})\s+displacement:(?P<displacement>{_FLOAT})"
    rf"(?:\s+samplesTaken:(?P<samples>\d+))?"
    rf"(?:\s+touchesUsed:(?P<touches>\d+))?"
    rf"(?:\s+id:(?P<point_id>.*))?"
)

_HOMED_RE = re.compile(
    r"homedStatusUpdate\s+x:(?P<x>-?\d+)\s+y:(?P<y>-?\d+)\s+z:(?P<z>-?\d+)"
)

_TOOL_RE = re.compile(
    r"toolUpdate\s+type:(?P<tool_type>\S+)(?:\s+version:(?P<version>\d+))?"
)

_MAXIMUM_Z_RE = re.compile(
    rf"setting maximum Z position to\s+(?P<z>{_FLOAT})",
    re.IGNORECASE,
)


def parse_position(line: str) -> Position | None:
    match = _POSITION_RE.search(line)
    if not match:
        return None
    return Position(
        x=float(match.group("x")),
        y=float(match.group("y")),
        z=float(match.group("z")),
        e=float(match.group("e")),
    )


def parse_temperature(line: str) -> Temperature | None:
    match = _TEMPERATURE_RE.search(line)
    if not match:
        return None
    return Temperature(
        current=float(match.group("current")),
        target=float(match.group("target")),
        time_remaining=float(match.group("remaining")),
    )


def parse_probe_measurement(line: str) -> ProbeMeasurement | None:
    match = _PROBE_RE.search(line)
    if not match:
        return None
    samples = match.group("samples")
    touches = match.group("touches")
    return ProbeMeasurement(
        x=float(match.group("x")),
        y=float(match.group("y")),
        z=float(match.group("z")),
        displacement=float(match.group("displacement")),
        samples_taken=int(samples) if samples is not None else None,
        touches_used=int(touches) if touches is not None else None,
        point_id=(match.group("point_id") or "").strip(),
    )


def parse_homed_status(line: str) -> HomedStatus | None:
    match = _HOMED_RE.search(line)
    if not match:
        return None
    return HomedStatus(
        x=int(match.group("x")),
        y=int(match.group("y")),
        z=int(match.group("z")),
    )


def parse_tool_status(line: str) -> ToolStatus | None:
    match = _TOOL_RE.search(line)
    if not match:
        return None
    version = match.group("version")
    return ToolStatus(
        tool_type=match.group("tool_type"),
        version=int(version) if version is not None else None,
    )


def parse_maximum_z_position(line: str) -> float | None:
    match = _MAXIMUM_Z_RE.search(line)
    if match is None:
        return None
    return float(match.group("z"))


def is_error_line(line: str) -> bool:
    text = line.lstrip("~ ").lower()
    return text.startswith("error:") or "missing characters detected" in text
