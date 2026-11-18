"""Offered load and SLO attainment over time, from the load generator's own records.

The replica counts and router signals in a report show what the autoscaler did;
these series show what it was asked to serve and whether it served it in time.
They are built from inference-perf's ``per_request_lifecycle_metrics.json``, one
record per request, binned by the time each request was sent:

  requests_sent              requests sent in the bin
  requests_errored           of those, requests that failed
  requests_errored_at_end    failed requests binned by when they ended, which
                             lines a burst up with what caused it (a pod removed
                             by a scale-down ends its in-flight requests together)
  offered_request_rate       requests_sent / bin width
  offered_input_token_rate   prompt tokens sent / bin width
  offered_output_budget_rate output tokens asked for (max_tokens) / bin width
  slo_attainment             requests that met every declared SLO constraint and
                             did not fail / requests_sent

The SLO is the run's own ``report.goodput.constraints``; with none declared there
is no attainment series. Each request is judged with inference-perf's formulas
(reportgen.base.compute_request_latency_metrics), so a request with no measurable
TTFT, or a failed one, counts as a miss. inference-perf's goodput_percentage
leaves failed requests out of its denominator; this one keeps them in.

inference-perf timestamps are ``time.perf_counter()`` values, so each run is
placed in UTC from the stage start and end epochs in
``inference-perf.partial.stage_<n>.yaml`` (inference-perf 0.7.0 loadgen):

- every rate-mode stage (``run_stage``: poisson, constant, concurrent,
  trace_replay) schedules its first request at least 1 s after its start epoch
  (perf_counter() + 1) and waits for every request before stamping its end, so
  no request ends after it;
- a session-replay stage (``run_session_stage``) sends from its start epoch with
  no lead, and stamps its end before the teardown that ends the requests still
  in flight, so only its sends precede the end epoch.

The offset is taken from the middle of the resulting bracket; its width is
recorded with the series.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

PER_REQUEST_FILE = "per_request_lifecycle_metrics.json"
PARTIAL_RE = re.compile(r"inference-perf\.partial\.stage_(\d+)\.yaml")
STAGE_FILE = "stage_{}_lifecycle_metrics.json"
# The request body is a JSON string; only its token budget is needed, and the
# prompt can be megabytes. The same words inside the prompt are escaped
# (\"max_tokens\"), so their closing quote never follows the name directly.
_BUDGET_RE = re.compile(r'"(?:max_tokens|max_completion_tokens)"\s*:\s*(\d+)')

DEFAULT_BIN_SECONDS = 30
# A JSON object the decoder still cannot finish after this much buffered text is
# a corrupt file, not a large request.
_MAX_BUFFER = 1 << 30
SESSION_LOAD_TYPES = frozenset({"trace_session_replay"})
RATE_LOAD_TYPES = frozenset({"poisson", "constant", "concurrent", "trace_replay"})
CLOSED_LOOP_LOAD_TYPES = frozenset({"concurrent", "trace_session_replay"})
LOAD_COMPONENT_LABEL = "Load generator (inference-perf)"
LOAD_COMPONENT_ID = "inference-perf"


class ClockAnchorError(ValueError):
    """The records cannot be placed in UTC consistently with the stage epochs."""


@dataclass
class Request:
    """The fields of one inference-perf record these series need."""

    start: float
    end: float
    failed: bool
    input_tokens: int
    output_budget: int | None
    output_tokens: int | None
    server_output_tokens: int | None
    first_token: float | None
    last_token: float | None
    token_times: int
    itl: float | None
    computed: dict[str, Any] | None


def _bin_seconds() -> float:
    try:
        value = float(
            os.environ.get(
                "LLMDBENCH_METRICS_LOAD_BIN_SECONDS",
                os.environ.get("METRICS_LOAD_BIN_SECONDS", DEFAULT_BIN_SECONDS),
            )
        )
    except ValueError:
        return float(DEFAULT_BIN_SECONDS)
    return value if value > 0 else float(DEFAULT_BIN_SECONDS)


def iter_json_array(path: Path, chunk_size: int = 1 << 22) -> Iterator[dict]:
    """Yield the objects of a top-level JSON array without loading it whole.

    The per-request file holds every prompt and response, so it reaches gigabytes;
    json.load would need several times that in memory in the harness pod.
    """
    decoder = json.JSONDecoder()
    buf = ""
    pos = 0
    started = False
    eof = False
    with open(path, encoding="utf-8") as fh:
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if not started:
                if pos < len(buf):
                    if buf[pos] != "[":
                        raise ValueError(f"{path}: not a JSON array")
                    started = True
                    pos += 1
                    continue
            elif pos < len(buf) and buf[pos] == "]":
                return
            elif pos < len(buf):
                try:
                    obj, end = decoder.raw_decode(buf, pos)
                except json.JSONDecodeError:
                    if eof or len(buf) - pos > _MAX_BUFFER:
                        raise
                else:
                    yield obj
                    pos = end
                    continue
            if eof:
                if started and pos >= len(buf):
                    raise ValueError(f"{path}: truncated JSON array")
                return
            buf = buf[pos:]
            pos = 0
            chunk = fh.read(chunk_size)
            if not chunk:
                eof = True
            buf += chunk


def _budget(body: Any) -> int | None:
    """The request's top-level output token budget, or None."""
    if isinstance(body, str):
        matches = set(_BUDGET_RE.findall(body))
        if len(matches) == 1:
            return int(matches.pop())
        if not matches:
            return None
        # A nested object (tools, response_format) also has the key: parse it.
        try:
            body = json.loads(body)
        except ValueError:
            return None
    if isinstance(body, dict):
        value = body.get("max_tokens")
        if value is None:
            value = body.get("max_completion_tokens")
        return int(value) if isinstance(value, (int, float)) else None
    return None


def _request(record: dict) -> Request:
    info = record.get("info") or {}
    text = (info.get("request_metrics") or {}).get("text") or {}
    response = info.get("response_metrics") or {}
    token_times = response.get("output_token_times") or []
    server = response.get("server_usage") or {}
    itl = None
    if len(token_times) > 1:
        deltas = [b - a for a, b in zip(token_times, token_times[1:])]
        itl = sum(deltas) / len(deltas)
    return Request(
        start=float(record["start_time"]),
        end=float(record["end_time"]),
        failed=record.get("error") is not None,
        input_tokens=int(text.get("input_tokens") or info.get("input_tokens") or 0),
        output_budget=_budget(record.get("request")),
        output_tokens=response.get("output_tokens"),
        server_output_tokens=server.get("completion_tokens"),
        first_token=token_times[0] if token_times else None,
        last_token=token_times[-1] if token_times else None,
        token_times=len(token_times),
        itl=itl,
        computed=record.get("computed_metrics"),
    )


def _stage_epochs(results_dir: Path) -> dict[int, tuple[float, float, int | None]]:
    """Stage index -> (start, end epoch seconds, requests) from the partial reports."""
    import yaml

    epochs: dict[int, tuple[float, float, int | None]] = {}
    for path in results_dir.iterdir():
        match = PARTIAL_RE.fullmatch(path.name)
        if not match:
            continue
        try:
            doc = yaml.safe_load(path.read_text()) or {}
            span = (doc.get("run") or {}).get("time") or {}
            start = datetime.fromisoformat(str(span["start"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(span["end"]).replace("Z", "+00:00"))
        except (OSError, KeyError, ValueError, TypeError, AttributeError):
            continue
        total = (
            (
                ((doc.get("results") or {}).get("request_performance") or {}).get(
                    "aggregate"
                )
                or {}
            ).get("requests")
            or {}
        ).get("total")
        epochs[int(match.group(1))] = (
            start.timestamp(),
            end.timestamp(),
            int(total) if isinstance(total, (int, float)) else None,
        )
    return epochs


def _load_type(results_dir: Path) -> str | None:
    """load.type from the harness config the run used, if it is there."""
    import yaml

    try:
        doc = yaml.safe_load((results_dir / "config.yaml").read_text()) or {}
    except (OSError, ValueError):
        return None
    value = (doc.get("load") or {}).get("type")
    return str(value) if value else None


def _schedule_delays(results_dir: Path, stages: list[int]) -> dict[str, Any]:
    """Per-stage send lateness (max, p99) from inference-perf's stage summaries.

    Requests sent late are not on the open-loop schedule: under overload a full
    worker pool can hold them back, and the send rate then understates demand.
    """
    delays: dict[str, Any] = {}
    for stage in stages:
        try:
            summary = json.loads((results_dir / STAGE_FILE.format(stage)).read_text())
        except (OSError, ValueError):
            continue
        delay = (summary.get("load_summary") or {}).get("schedule_delay") or {}
        if "max" in delay:
            delays[str(stage)] = {"max": delay.get("max"), "p99": delay.get("p99")}
    return delays


def _bracket(
    requests: list[Request],
    epochs: dict[int, tuple[float, float, int | None]],
    load_type: str | None,
) -> tuple[float, float, str]:
    """Bounds on epoch - perf_counter, and how they were found.

    Stages send one after another, so when the partial reports' request counts
    add up, the sorted requests split into stages by count and each stage bounds
    the offset by its own start and end; otherwise only the first and the last
    stage do, which is looser. A rate-mode stage cut short (a circuit breaker)
    can still have requests in flight at its end, so if the completion bound
    contradicts the start bound the send bound is used instead.
    """
    session = load_type in SESSION_LOAD_TYPES
    # An unknown type gets no lead and the send bound: looser, never wrong.
    lead = 1.0 if load_type in RATE_LOAD_TYPES else 0.0
    by_end = load_type in RATE_LOAD_TYPES

    def bounds(parts: list[tuple[float, float, list[Request]]], use_end: bool):
        low, high = -math.inf, math.inf
        for start, end, part in parts:
            if part:
                low = max(low, start + lead - part[0].start)
                last = max(q.end for q in part) if use_end else part[-1].start
                high = min(high, end - last)
        return low, high

    stages = [epochs[s] for s in sorted(epochs)]
    totals = [t for _, _, t in stages]
    if all(t is not None for t in totals) and sum(totals) == len(requests):
        parts, i = [], 0
        for start, end, total in stages:
            parts.append((start, end, requests[i : i + total]))
            i += total
        how = "per stage"
    else:
        parts = [(stages[0][0], stages[-1][1], requests)]
        how = "first and last stage"
    low, high = bounds(parts, by_end)
    if by_end and low > high:
        low, high = bounds(parts, False)
        how += ", send bound"
    kind = "session" if session else ("rate" if by_end else "unknown load type")
    return low, high, f"inference-perf partial stage epochs, {how} ({kind})"


@dataclass
class LoadGeneratorRequests:
    """One run's requests, placed in UTC."""

    requests: list[Request]
    offset: float
    offset_uncertainty: float
    clock: str
    load_type: str | None = None
    schedule_delay: dict[str, Any] = field(default_factory=dict)
    mean_input_tokens: float | None = None


def load_generator_requests(results_dir: str | Path) -> LoadGeneratorRequests | None:
    """Read inference-perf's per-request records and place them in UTC.

    Returns None when there is nothing to read: no per-request file (another
    harness, or a re-analysis after the file was archived) or no partial stage
    reports. Raises ClockAnchorError when the records and the stage epochs cannot
    both hold, so the caller can say why the series is missing.
    """
    results_dir = Path(results_dir)
    path = results_dir / PER_REQUEST_FILE
    if not path.is_file():
        return None
    epochs = _stage_epochs(results_dir)
    if not epochs:
        return None
    requests = sorted(
        (_request(r) for r in iter_json_array(path) if isinstance(r, dict)),
        key=lambda q: q.start,
    )
    if not requests:
        return None
    load_type = _load_type(results_dir)
    low, high, clock = _bracket(requests, epochs, load_type)
    if low > high:
        raise ClockAnchorError(
            f"{len(requests)} requests do not fit the {len(epochs)} stage epochs "
            f"({clock}: bounds {low - high:.3f} s apart); a retried run or "
            f"mismatched partial reports"
        )
    ok = [q.input_tokens for q in requests if not q.failed and q.input_tokens]
    return LoadGeneratorRequests(
        requests=requests,
        offset=(low + high) / 2,
        offset_uncertainty=(high - low) / 2,
        clock=clock,
        load_type=load_type,
        schedule_delay=_schedule_delays(results_dir, sorted(epochs)),
        mean_input_tokens=sum(ok) / len(ok) if ok else None,
    )


# ---------------------------------------------------------------------------
# Per-request SLO verdict, with inference-perf's formulas
# ---------------------------------------------------------------------------

_CONSTRAINTS = ("ttft", "tpot", "itl", "ntpot", "request_latency")


def _latencies(q: Request, use_server_output_tokens: bool) -> dict[str, float | None]:
    """compute_request_latency_metrics for one request (None where undefined)."""
    if q.failed:
        return dict.fromkeys(_CONSTRAINTS)
    if q.computed:
        return {
            "ttft": q.computed.get("time_to_first_token"),
            "tpot": q.computed.get("time_per_output_token"),
            "itl": q.computed.get("inter_token_latency"),
            "ntpot": q.computed.get("normalized_time_per_output_token"),
            "request_latency": q.computed.get("request_latency"),
        }
    tokens = q.output_tokens or 0
    if use_server_output_tokens and q.server_output_tokens is not None:
        tokens = q.server_output_tokens
    latency = q.end - q.start
    out: dict[str, float | None] = {
        "ttft": None,
        "tpot": None,
        "itl": None,
        "ntpot": latency / tokens if tokens > 0 else 0.0,
        "request_latency": latency,
    }
    # Streamable only with more than one output token timestamp.
    if q.token_times > 1 and q.first_token is not None and q.last_token is not None:
        out["ttft"] = q.first_token - q.start
        if tokens > 1:
            out["tpot"] = (q.last_token - q.first_token) / (tokens - 1)
        out["itl"] = q.itl
    return out


def met_slo(
    q: Request, constraints: dict[str, float], use_server_output_tokens: bool
) -> bool:
    """True when the request did not fail and meets every declared constraint."""
    if q.failed:
        return False
    values = _latencies(q, use_server_output_tokens)
    for name, limit in constraints.items():
        value = values.get(name)
        if value is None or value > limit:
            return False
    return True


# ---------------------------------------------------------------------------
# Report component
# ---------------------------------------------------------------------------


def _native_config(br_dict: dict[str, Any]) -> dict[str, Any]:
    return (
        ((br_dict.get("scenario") or {}).get("load") or {}).get("native") or {}
    ).get("config") or {}


def build_load_component(
    br_dict: dict[str, Any],
    loaded: LoadGeneratorRequests,
    window: tuple[datetime, datetime] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The load-generator component for one report, and its metadata.

    Bins are anchored to the window start (or the first send) so they line up with
    the stage-clipped replica series. Every bin gets an offered value, 0 when
    nothing was sent: an empty bin is a gap in the load, and leaving it out would
    let a viewer draw a line across it. Attainment has no value in an empty bin.
    """
    config = _native_config(br_dict)
    report = config.get("report") or {}
    goodput = report.get("goodput") or {}
    constraints = {
        k: float(v)
        for k, v in (goodput.get("constraints") or {}).items()
        if k in _CONSTRAINTS and isinstance(v, (int, float))
    }
    use_server = bool(
        (report.get("request_lifecycle") or {}).get("use_server_output_tokens")
    )
    ignore_eos = bool((config.get("server") or {}).get("ignore_eos"))
    load_type = (config.get("load") or {}).get("type") or loaded.load_type

    def utc(t: float) -> float:
        return t + loaded.offset

    if window is not None:
        lo, hi = window[0].timestamp(), window[1].timestamp()
        requests = [q for q in loaded.requests if lo <= utc(q.start) < hi]
        origin, span_end = lo, hi
    else:
        requests = loaded.requests
        origin = math.floor(utc(requests[0].start)) if requests else 0.0
        span_end = max(utc(q.end) for q in requests) + 1e-9 if requests else 0.0

    width = _bin_seconds()
    meta: dict[str, Any] = {
        "harness": "inference-perf",
        "bin_seconds": width,
        "binned_by": "request send time (requests_errored_at_end: end time)",
        "clock": loaded.clock,
        "clock_uncertainty_seconds": round(loaded.offset_uncertainty, 3),
        "load_type": load_type,
        "loop": (
            None
            if load_type is None
            else "closed"
            if load_type in CLOSED_LOOP_LOAD_TYPES
            else "open"
        ),
        "schedule_delay_seconds": loaded.schedule_delay or None,
        "slo_constraints_seconds": constraints or None,
        "requests": len(requests),
    }
    if not requests:
        return None, meta

    count = max(1, math.ceil((span_end - origin) / width))
    sent = [0] * count
    errored = [0] * count
    errored_at_end = [0] * count
    in_tokens = [0.0] * count
    budget = [0] * count
    good = [0] * count
    input_estimated = budget_missing = budget_from_output = 0
    for q in requests:
        i = min(count - 1, max(0, int((utc(q.start) - origin) // width)))
        sent[i] += 1
        if q.failed:
            errored[i] += 1
            j = int((utc(q.end) - origin) // width)
            if 0 <= j < count:
                errored_at_end[j] += 1
        tokens: float = q.input_tokens
        if not tokens and q.failed and loaded.mean_input_tokens:
            # inference-perf records 0 prompt tokens for a failed request.
            tokens = loaded.mean_input_tokens
            input_estimated += 1
        in_tokens[i] += tokens
        value = q.output_budget
        if value is None and ignore_eos and not q.failed:
            # No request body kept; with ignore_eos the server generates exactly
            # the budget, so what it generated is what was asked for.
            value = q.server_output_tokens or q.output_tokens
            budget_from_output += value is not None
        if value is None:
            budget_missing += 1
        else:
            budget[i] += value
        if constraints and met_slo(q, constraints, use_server):
            good[i] += 1

    def ts(i: int) -> str:
        return datetime.fromtimestamp(origin + i * width, tz=timezone.utc).isoformat()

    def series(values: list[float], units: str) -> dict[str, Any]:
        return {
            "units": units,
            "series": [{"ts": ts(i), "value": v} for i, v in enumerate(values)],
        }

    time_series: dict[str, Any] = {
        "requests_sent": series(sent, "count"),
        "requests_errored": series(errored, "count"),
        "requests_errored_at_end": series(errored_at_end, "count"),
        "offered_request_rate": series([n / width for n in sent], "queries/s"),
        "offered_input_token_rate": series([n / width for n in in_tokens], "tokens/s"),
    }
    if budget_missing < len(requests):
        time_series["offered_output_budget_rate"] = series(
            [n / width for n in budget], "tokens/s"
        )
    if constraints:
        time_series["slo_attainment"] = {
            "units": "fraction",
            "series": [
                {"ts": ts(i), "value": good[i] / sent[i]}
                for i in range(count)
                if sent[i]
            ],
        }
    if input_estimated:
        meta["failed_requests_with_estimated_input_tokens"] = input_estimated
    if budget_from_output:
        meta["output_budget_from_generated_tokens"] = budget_from_output
    if budget_missing:
        meta["requests_without_output_budget"] = budget_missing
    component = {
        "component_label": LOAD_COMPONENT_LABEL,
        "replica_id": LOAD_COMPONENT_ID,
        "time_series": time_series,
    }
    return component, meta


LOAD_TIME_SERIES_FIELDS = frozenset(
    (
        "requests_sent",
        "requests_errored",
        "requests_errored_at_end",
        "offered_request_rate",
        "offered_input_token_rate",
        "offered_output_budget_rate",
        "slo_attainment",
    )
)

__all__ = [
    "ClockAnchorError",
    "LOAD_TIME_SERIES_FIELDS",
    "LoadGeneratorRequests",
    "build_load_component",
    "iter_json_array",
    "load_generator_requests",
    "met_slo",
]
