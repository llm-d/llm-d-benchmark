"""Line format for metric-time-series 0.1.

Each series is declared once, the first time a scrape carries it. Each scrape is
one line holding a value for every declared series, in declaration order:

    {"series":0,"name":"vllm:num_requests_running","pod":"decode-0","role":"decode","type":"gauge"}
    {"series":1,"name":"vllm:prompt_tokens_total","pod":"decode-0","role":"decode","type":"counter"}
    {"t0":"2026-11-17T10:00:00+00:00"}
    {"dt":0,"v":[0,0]}
    {"dt":15000000,"v":[4,51200]}
    {"dt":30000000,"v":[0,0],"raw":[1]}

``dt`` is microseconds since ``t0``. Gauges are stored as is. A counter is
stored as its increase since the series' previous value, so an idle counter is
0 and a run of them gzips to almost nothing. ``raw`` lists the counters stored
as is on that line: after a reset, or when the increase would not add back to
the exact value. A series missing from a scrape is null. NaN and infinities are
the strings "NaN", "+Inf" and "-Inf".
"""

import datetime
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple

from ...stream import require_zone

_MICROSECOND = datetime.timedelta(microseconds=1)
_NON_FINITE = {"NaN": math.nan, "+Inf": math.inf, "-Inf": -math.inf}


class SeriesType(StrEnum):
    """How values of a series relate over time."""

    GAUGE = "gauge"
    """Any value at any time."""
    COUNTER = "counter"
    """Only grows, except when its process restarts and it starts over."""


class SeriesKey(NamedTuple):
    """Identity of one series. Pod and role are carried here, not looked up elsewhere."""

    name: str
    """Raw Prometheus name, e.g. ``vllm:num_requests_running``."""
    labels: tuple[tuple[str, str], ...] = ()
    """Label pairs, sorted by name."""
    pod: str | None = None
    role: str | None = None
    type: SeriesType = SeriesType.GAUGE

    @classmethod
    def of(
        cls,
        name: str,
        labels: Mapping[str, str] | None = None,
        pod: str | None = None,
        role: str | None = None,
        type: SeriesType = SeriesType.GAUGE,  # pylint: disable=redefined-builtin
    ) -> "SeriesKey":
        """A key with its labels sorted, so the same series always gets the same key."""
        return cls(name, tuple(sorted((labels or {}).items())), pod, role, type)


@dataclass(frozen=True)
class Scrape:
    """Every value read at one instant. A series left out was not read."""

    ts: datetime.datetime
    values: dict[SeriesKey, float]

    def __post_init__(self) -> None:
        require_zone(self.ts)


def _num(x: float) -> int | float | str:
    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "+Inf" if x > 0 else "-Inf"
    # whole numbers as ints, which is shorter, while they still convert back exactly
    if x.is_integer() and abs(x) < 2**53:
        return int(x)
    return x


def _from_num(x: int | float | str) -> float:
    return _NON_FINITE[x] if isinstance(x, str) else float(x)


def _declaration(series: int, key: SeriesKey) -> dict[str, Any]:
    line: dict[str, Any] = {"series": series, "name": key.name}
    if key.labels:
        line["labels"] = dict(key.labels)
    if key.pod is not None:
        line["pod"] = key.pod
    if key.role is not None:
        line["role"] = key.role
    line["type"] = key.type.value
    return line


class ScrapeEncoder:
    """Turns scrapes into lines. Holds the series table and each series' last value."""

    def __init__(self) -> None:
        self._ids: dict[SeriesKey, int] = {}
        self._keys: list[SeriesKey] = []
        self._last: list[float | None] = []
        self._t0: datetime.datetime | None = None

    def encode(self, record: Scrape) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        for key in record.values:
            if key not in self._ids:
                if list(key.labels) != sorted(key.labels):
                    raise ValueError(
                        f"Labels of {key.name} are not sorted, use SeriesKey.of()"
                    )
                self._ids[key] = len(self._keys)
                self._keys.append(key)
                self._last.append(None)
                lines.append(_declaration(self._ids[key], key))
        if self._t0 is None:
            self._t0 = record.ts
            lines.append({"t0": record.ts.isoformat()})
        values: list[int | float | str | None] = []
        raw: list[int] = []
        for i, key in enumerate(self._keys):
            if key not in record.values:
                values.append(None)
                continue
            value = float(record.values[key])
            last = self._last[i]
            if key.type is SeriesType.COUNTER and last is not None:
                if _adds_back(last, value):
                    values.append(_num(value - last))
                else:
                    values.append(_num(value))
                    raw.append(i)
            else:
                values.append(_num(value))
            self._last[i] = value
        line: dict[str, Any] = {"dt": (record.ts - self._t0) // _MICROSECOND}
        line["v"] = values
        if raw:
            line["raw"] = raw
        return [*lines, line]


def _adds_back(last: float, value: float) -> bool:
    # a counter that went down restarted. Float subtraction can round, so the
    # increase is kept only when last + increase gives back exactly value.
    return (
        math.isfinite(last)
        and math.isfinite(value)
        and value >= last
        and last + (value - last) == value
    )


class ScrapeDecoder:
    """Reads lines back into scrapes, undoing what ScrapeEncoder did."""

    def __init__(self) -> None:
        self._keys: list[SeriesKey] = []
        self._last: list[float | None] = []
        self._t0: datetime.datetime | None = None

    def decode(self, line: dict[str, Any]) -> Scrape | None:
        if "series" in line:
            if line["series"] != len(self._keys):
                raise ValueError(f"Series {line['series']} declared out of order")
            self._keys.append(
                SeriesKey(
                    name=line["name"],
                    labels=tuple(sorted(line.get("labels", {}).items())),
                    pod=line.get("pod"),
                    role=line.get("role"),
                    type=SeriesType(line["type"]),
                )
            )
            self._last.append(None)
            return None
        if "t0" in line:
            self._t0 = require_zone(datetime.datetime.fromisoformat(line["t0"]))
            return None
        if self._t0 is None:
            raise ValueError("Scrape line before the t0 line")
        stored = line["v"]
        if len(stored) != len(self._keys):
            raise ValueError(
                f"Scrape has {len(stored)} values for {len(self._keys)} series"
            )
        raw = set(line.get("raw", ()))
        values: dict[SeriesKey, float] = {}
        for i, x in enumerate(stored):
            if x is None:
                continue
            value = _from_num(x)
            last = self._last[i]
            key = self._keys[i]
            if key.type is SeriesType.COUNTER and last is not None and i not in raw:
                value = last + value
            self._last[i] = value
            values[key] = value
        return Scrape(ts=self._t0 + line["dt"] * _MICROSECOND, values=values)
