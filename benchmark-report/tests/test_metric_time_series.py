"""metric-time-series 0.1: the line format and the model built from it."""

import datetime
import gzip
import math
from pathlib import Path

import pytest

from llmd_benchmark_report.artifacts import (
    LocalFileSink,
    Run,
    artifact_model,
    load_artifact,
    read_stream,
)
from llmd_benchmark_report.artifacts.metric_time_series.v0_1.codec import (
    Scrape,
    ScrapeDecoder,
    ScrapeEncoder,
    SeriesKey,
    SeriesType,
)
from llmd_benchmark_report.artifacts.metric_time_series.v0_1.schema import (
    MetricTimeSeriesV01,
)

T0 = datetime.datetime(2026, 11, 17, 10, 0, tzinfo=datetime.UTC)
RUNNING = SeriesKey.of("vllm:num_requests_running", pod="decode-0", role="decode")
PROMPT = SeriesKey.of(
    "vllm:prompt_tokens_total", pod="decode-0", role="decode", type=SeriesType.COUNTER
)


# Scrape at T0 plus `seconds`, with the given values.
def _at(seconds: float, values: dict[SeriesKey, float]) -> Scrape:
    return Scrape(ts=T0 + datetime.timedelta(seconds=seconds), values=values)


# Every line the encoder writes for `scrapes`, in order.
def _encode(scrapes: list[Scrape]) -> list[dict]:
    encoder = ScrapeEncoder()
    return [line for scrape in scrapes for line in encoder.encode(scrape)]


# The scrapes a decoder reads back from `lines`.
def _decode(lines: list[dict]) -> list[Scrape]:
    decoder = ScrapeDecoder()
    return [s for line in lines if (s := decoder.decode(line)) is not None]


# Idle, then 4 running and 51200 prompt tokens, then a restart that zeroes both.
# Pins the documented lines: series declared once, t0, dt in microseconds, the
# counter stored as its increase, and the restart stored as is under raw.
def test_line_format() -> None:
    lines = _encode(
        [
            _at(0, {RUNNING: 0, PROMPT: 0}),
            _at(15, {RUNNING: 4, PROMPT: 51200}),
            _at(30, {RUNNING: 0, PROMPT: 0}),
        ]
    )
    assert lines == [
        {
            "series": 0,
            "name": "vllm:num_requests_running",
            "pod": "decode-0",
            "role": "decode",
            "type": "gauge",
        },
        {
            "series": 1,
            "name": "vllm:prompt_tokens_total",
            "pod": "decode-0",
            "role": "decode",
            "type": "counter",
        },
        {"t0": "2026-11-17T10:00:00+00:00"},
        {"dt": 0, "v": [0, 0]},
        {"dt": 15_000_000, "v": [4, 51200]},
        {"dt": 30_000_000, "v": [0, 0], "raw": [1]},
    ]


# A counter idle for three scrapes after 100 tokens stores 0 three times.
def test_an_idle_counter_stores_zeros() -> None:
    lines = _encode([_at(t, {PROMPT: 100}) for t in (0, 15, 30, 45)])
    assert [line["v"] for line in lines if "v" in line] == [[100], [0], [0], [0]]


# A counter going 0.20541132487436498 -> 5.093201308222221 cannot be stored as
# an increase, since last + increase rounds to 5.093201308222222. It is stored
# as is under raw and reads back exactly.
def test_a_float_increase_that_rounds_is_stored_as_is() -> None:
    before, after = 0.20541132487436498, 5.093201308222221
    assert before + (after - before) != after
    scrapes = [_at(0, {PROMPT: before}), _at(15, {PROMPT: after})]
    lines = _encode(scrapes)
    assert lines[-1] == {"dt": 15_000_000, "v": [after], "raw": [0]}
    assert _decode(lines) == scrapes


# Gauge NaN, +Inf and -Inf are stored as "NaN", "+Inf", "-Inf" and read back.
def test_non_finite_values() -> None:
    lines = _encode(
        [
            _at(0, {RUNNING: math.nan}),
            _at(1, {RUNNING: math.inf}),
            _at(2, {RUNNING: -math.inf}),
        ]
    )
    assert [line["v"] for line in lines if "v" in line] == [["NaN"], ["+Inf"], ["-Inf"]]
    values = [s.values[RUNNING] for s in _decode(lines)]
    assert math.isnan(values[0]) and values[1:] == [math.inf, -math.inf]


# A counter missing from the middle scrape is null there, and the next
# increase is taken from its last seen value: 10, missing, 25 stores 10, null, 15.
def test_a_missing_value_is_null() -> None:
    scrapes = [
        _at(0, {PROMPT: 10, RUNNING: 1}),
        _at(15, {RUNNING: 2}),
        _at(30, {PROMPT: 25, RUNNING: 3}),
    ]
    lines = _encode(scrapes)
    assert [line["v"] for line in lines if "v" in line] == [[10, 1], [None, 2], [15, 3]]
    assert _decode(lines) == scrapes


# A series that first shows up in the third scrape is declared on that line,
# reads as null on no earlier line, and every scrape reads back exactly.
def test_a_series_can_appear_mid_run() -> None:
    late = SeriesKey.of("vllm:num_requests_running", pod="decode-1", role="decode")
    scrapes = [
        _at(0, {RUNNING: 1}),
        _at(15, {RUNNING: 2}),
        _at(30, {RUNNING: 3, late: 7}),
    ]
    lines = _encode(scrapes)
    assert lines[-2]["series"] == 1
    assert lines[-1]["v"] == [3, 7]
    assert _decode(lines) == scrapes


# Mixed load over 40 scrapes on 2 pods, a counter restart at scrape 20 and
# float _sum counters: decode(encode(x)) == x for every value and timestamp.
def test_round_trip_is_exact() -> None:
    keys = [
        SeriesKey.of(
            name, {"le": le} if le else None, pod=pod, role="decode", type=kind
        )
        for pod in ("decode-0", "decode-1")
        for name, le, kind in (
            ("vllm:kv_cache_usage_perc", None, SeriesType.GAUGE),
            ("vllm:e2e_request_latency_seconds_bucket", "1.0", SeriesType.COUNTER),
            ("vllm:e2e_request_latency_seconds_sum", None, SeriesType.COUNTER),
        )
    ]
    totals = {key: 0.0 for key in keys}
    scrapes = []
    for k in range(40):
        for i, key in enumerate(keys):
            if key.type is SeriesType.GAUGE:
                totals[key] = (k * 7 + i) % 10 / 13
            elif k == 20 and key.pod == "decode-1":
                totals[key] = 0.0
            else:
                totals[key] += (k % 5) * (0.1 + i / 7)
        scrapes.append(_at(k * 15, dict(totals)))
    assert _decode(_encode(scrapes)) == scrapes


# A scrape at a time with no zone is refused.
def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError, match="has no zone"):
        Scrape(ts=datetime.datetime(2026, 11, 17, 10, 0), values={})


# A key built by hand with labels out of order is refused. SeriesKey.of sorts them.
def test_labels_must_be_sorted() -> None:
    unsorted = SeriesKey("m", (("pod", "a"), ("engine", "0")))
    with pytest.raises(ValueError, match="not sorted"):
        ScrapeEncoder().encode(_at(0, {unsorted: 1}))
    assert SeriesKey.of("m", {"pod": "a", "engine": "0"}).labels == (
        ("engine", "0"),
        ("pod", "a"),
    )


# Two scrapes written to a file read back as one artifact with one series per
# key, each holding both points. On disk the second scrape is the compact line
# {"dt":15000000,"v":[2,200]}. The artifact is registered, validates with no
# issues, and load_artifact() rebuilds it from its dump.
def test_model_from_a_stream(tmp_path: Path) -> None:
    path = tmp_path / "metrics.jsonl.gz"
    with MetricTimeSeriesV01.open(LocalFileSink(path), Run(uid="m1")) as writer:
        writer.write(_at(0, {RUNNING: 1, PROMPT: 100}))
        writer.write(_at(15, {RUNNING: 2, PROMPT: 300}))
    artifact, complete = MetricTimeSeriesV01.read(path.read_bytes())
    assert complete
    assert read_stream(path.read_bytes()).end == {"records": 2}
    assert '{"dt":15000000,"v":[2,200]}' in gzip.decompress(path.read_bytes()).decode()
    by_name = {s.name: [p.value for p in s.points] for s in artifact.series}
    assert by_name == {
        "vllm:num_requests_running": [1, 2],
        "vllm:prompt_tokens_total": [100, 300],
    }
    assert artifact.validate() == []
    assert artifact_model("metric-time-series", "0.1") is MetricTimeSeriesV01
    assert load_artifact(artifact.model_dump(mode="json")) == artifact
