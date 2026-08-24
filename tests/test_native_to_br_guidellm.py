"""Converter test: GuideLLM native report -> benchmark report v0.1 / v0.2.

The input fixture (tests/fixtures/guidellm_report_v2.json) is genuine GuideLLM
output, captured by running ``guidellm run`` against GuideLLM's own
``mock-server`` with a two-stage ``constant`` profile, multi-turn synthetic
text, and a prefix bucket. Only the per-request payload dumps and the
per-token ``text``/``audio``/``image``/``video``/``tool_call`` metric trees --
none of which the converters read -- were stripped, to keep the fixture a
reasonable size.

These tests pin the v0.7 report-format migration. GuideLLM v0.7 renamed the
report's top-level ``args`` object to a restructured ``config``
(``BenchmarkScenario``), which made both converters raise ``KeyError: 'args'``
on every run. The run-wide flat ``profile``/``rate`` pair became a nested
profile object, the ``data`` entries became dicts instead of YAML strings,
the model moved under ``backend``, and the synthetic ``prefix_tokens``/
``prefix_count`` pair became ``prefix_buckets``. See guidellm_native.py.

Per-stage rate and concurrency are asserted against the benchmark's own
realized ``config.strategy`` rather than the run-wide profile, since that is
what the converter reads and what actually ran.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmdbenchmark.analysis.benchmark_report import guidellm_native
from llmdbenchmark.analysis.benchmark_report.base import Units
from llmdbenchmark.analysis.benchmark_report.native_to_br0_1 import (
    import_guidellm as import_guidellm_v01,
)
from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
    import_guidellm as import_guidellm_v02,
)
from llmdbenchmark.analysis.benchmark_report.native_to_br0_2_1 import (
    import_guidellm as import_guidellm_v021,
)
from llmdbenchmark.analysis.benchmark_report.schema_v0_1 import BenchmarkReportV01
from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import (
    BenchmarkReportV02,
    Distribution,
    LoadSource,
)

FIXTURE = Path(__file__).parent / "fixtures" / "guidellm_report_v2.json"


@pytest.fixture(scope="module")
def native() -> dict:
    with open(FIXTURE) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def report() -> BenchmarkReportV02:
    return import_guidellm_v02(str(FIXTURE))


# ---------------------------------------------------------------------------
# The fixture is the format we claim it is
# ---------------------------------------------------------------------------


def test_fixture_is_report_schema_v2(native):
    """Guards the premise of every other test here: a v1-format fixture
    would let a converter that only reads ``args`` pass."""
    assert native["metadata"]["version"] == 2
    assert "config" in native
    assert "args" not in native
    assert len(native["benchmarks"]) == 2


# ---------------------------------------------------------------------------
# v0.2 converter
# ---------------------------------------------------------------------------


def test_returns_v0_2_report(report):
    assert isinstance(report, BenchmarkReportV02)
    assert report.version == "0.2"


def test_native_config_captures_run_wide_config(report, native):
    """With no workload file to load, the native section must fall back to the
    report's own config block -- ``config`` on v2, not the removed ``args``."""
    assert report.scenario.load.native.config == native["config"]


def test_input_seq_len_from_nested_data_spec(report, native):
    """The synthetic data spec moved to ``config.spec.data`` and is a dict
    now, not a YAML string needing a second parse."""
    data = native["config"]["spec"]["data"][0]
    isl = report.scenario.load.standardized.input_seq_len
    assert isl.value == data["prompt_tokens"]
    assert isl.std_dev == data["prompt_tokens_stdev"]
    assert isl.min == data["prompt_tokens_min"]
    assert isl.max == data["prompt_tokens_max"]
    # A stated stdev makes it Gaussian, regardless of the min/max clamps.
    assert isl.distribution == Distribution.GAUSSIAN


def test_output_seq_len_fixed(report, native):
    data = native["config"]["spec"]["data"][0]
    osl = report.scenario.load.standardized.output_seq_len
    assert osl.value == data["output_tokens"]
    assert osl.distribution == Distribution.FIXED


def test_prefix_from_prefix_buckets(report, native):
    """v0.7 replaced flat ``prefix_tokens``/``prefix_count`` with buckets."""
    bucket = native["config"]["spec"]["data"][0]["prefix_buckets"][0]
    prefix = report.scenario.load.standardized.prefix
    assert prefix is not None
    assert prefix.prefix_len.value == bucket["prefix_tokens"]
    assert prefix.prefix_len.distribution == Distribution.FIXED
    assert prefix.num_prefixes == bucket["prefix_count"]


def test_multi_turn_from_turns(report, native):
    turns = native["config"]["spec"]["data"][0]["turns"]
    assert turns > 1, "fixture must be multi-turn for this to mean anything"
    multi_turn = report.scenario.load.standardized.multi_turn
    assert multi_turn is not None
    assert multi_turn.enabled is True
    assert multi_turn.max_turns.value == turns


def test_synthetic_text_is_random_source(report):
    """synthetic_text generates tokens rather than sampling a dataset. On the
    old format this was inferred from a ``source`` key that no longer exists."""
    assert report.scenario.load.standardized.source == LoadSource.RANDOM


@pytest.mark.parametrize("index", [0, 1])
def test_rate_from_realized_per_stage_strategy(native, index):
    """Each stage's rate comes from its own realized strategy, not from
    indexing the run-wide rate list the old converter used."""
    strategy = native["benchmarks"][index]["config"]["strategy"]
    assert strategy["type_"] == "constant"

    standardized = import_guidellm_v02(str(FIXTURE), index).scenario.load.standardized
    assert standardized.rate_qps == strategy["rate"]
    # A rated profile does not pin concurrency.
    assert standardized.concurrency is None
    assert standardized.stage == index


@pytest.mark.parametrize("index", [0, 1])
def test_request_totals_and_timing(native, index):
    """The per-benchmark metric tree did not change in v0.7; pin that the
    fix did not disturb it."""
    results = native["benchmarks"][index]
    report = import_guidellm_v02(str(FIXTURE), index)

    requests = report.results.request_performance.aggregate.requests
    totals = results["metrics"]["request_totals"]
    assert requests.total == totals["total"]
    assert requests.failures == totals["errored"]
    assert requests.incomplete == totals["incomplete"]

    assert report.run.time.duration == f"PT{results['duration']}S"


def test_latency_percentiles_mapped(native):
    results = native["benchmarks"][0]
    report = import_guidellm_v02(str(FIXTURE))
    raw = results["metrics"]["request_latency"]["successful"]
    latency = report.results.request_performance.aggregate.latency.request_latency
    assert latency.mean == raw["mean"]
    assert latency.min == raw["min"]
    assert latency.max == raw["max"]
    assert latency.stddev == raw["std_dev"]
    assert latency.p50 == raw["percentiles"]["p50"]
    assert latency.p99 == raw["percentiles"]["p99"]


def test_latency_units_match_the_native_scale():
    """The converter passes latencies through unscaled, so each field's unit
    has to match whatever scale guidellm emitted.

    guidellm suffixes most of its latency metrics ``_ms``, but
    ``request_latency`` alone is seconds (schemas/request_stats.py: "End-to-end
    request processing latency in seconds"; its own summary table prints a
    "Sec" column for it). It was labelled ``ms`` regardless, which made a
    report self-contradictory: TTFT in ms exceeded the end-to-end latency it is
    a component of. Values were never wrong -- only the unit -- so a value
    assertion cannot catch this and the units are pinned separately.
    """
    report = import_guidellm_v02(str(FIXTURE))
    latency = report.results.request_performance.aggregate.latency

    assert latency.request_latency.units == Units.S
    assert latency.time_to_first_token.units == Units.MS
    assert latency.inter_token_latency.units == Units.MS_PER_TOKEN
    assert latency.time_per_output_token.units == Units.MS_PER_TOKEN

    # request_latency is the only one holding seconds, so it is the only one
    # whose magnitude should be ~1000x smaller than a TTFT measured in ms.
    assert latency.request_latency.mean < latency.time_to_first_token.mean


def test_v0_1_latency_units_match_the_native_scale():
    """The v0.1 converter has its own copy of the unit table."""
    report = import_guidellm_v01(str(FIXTURE))
    latency = report.metrics.latency

    assert latency.request_latency.units == Units.S
    assert latency.time_to_first_token.units == Units.MS
    assert latency.inter_token_latency.units == Units.MS_PER_TOKEN
    assert latency.time_per_output_token.units == Units.MS_PER_TOKEN


def test_v0_2_1_shares_the_fix():
    """native_to_br0_2_1 re-exports the v0.2 guidellm converter unchanged, so
    the same fix has to cover it. It reports version 0.2 because it is
    literally the same function object."""
    assert import_guidellm_v021 is import_guidellm_v02
    assert import_guidellm_v021(str(FIXTURE)).version == "0.2"


# ---------------------------------------------------------------------------
# v0.1 converter
# ---------------------------------------------------------------------------


def test_v0_1_reads_model_and_config(native):
    """The model moved from the flat ``args.model`` to ``backend.model``;
    reading the old path yielded "unknown" at best, KeyError at worst."""
    report = import_guidellm_v01(str(FIXTURE))
    assert isinstance(report, BenchmarkReportV01)
    assert (
        report.scenario.model.name
        == (native["benchmarks"][0]["config"]["backend"]["model"])
    )
    assert report.scenario.load.args == native["config"]
    assert report.metrics.time.duration == native["benchmarks"][0]["duration"]


# ---------------------------------------------------------------------------
# Backward compatibility with the pre-v0.7 format
# ---------------------------------------------------------------------------


def _as_report_schema_v1(native: dict) -> dict:
    """Rewrite a v2 fixture into the pre-v0.7 shape the old converter read.

    Mirrors guidellm v0.6.1's ``BenchmarkGenerativeTextArgs``, verified
    against that release: a flat ``args`` block whose ``profile`` is a bare
    name, whose ``rate`` is a list, whose ``data`` entries are the raw
    ``--data`` strings (needing a second parse), and which carries the model
    under ``backend_kwargs`` rather than at the top level. The per-benchmark
    tree is left untouched, which is the point -- it did not change in v0.7.
    """
    spec = native["config"]["spec"]
    return {
        "metadata": {"version": 1},
        "args": {
            "backend": "openai_http",
            "backend_kwargs": {
                "target": spec["backend"]["target"],
                "model": spec["backend"]["model"],
            },
            "profile": spec["profile"]["kind"],
            "rate": spec["profile"]["rate"],
            "data": [json.dumps(spec["data"][0])],
        },
        "benchmarks": native["benchmarks"],
    }


@pytest.mark.parametrize("index", [0, 1])
def test_pre_v0_7_format_still_converts(tmp_path, native, index):
    """The fix must not trade one broken format for another: reports written
    by guidellm <= v0.6.x still have to convert."""
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(_as_report_schema_v1(native)), encoding="utf-8")

    report = import_guidellm_v02(str(legacy), index)
    standardized = report.scenario.load.standardized
    expected_rate = native["config"]["spec"]["profile"]["rate"][index]
    assert standardized.rate_qps == expected_rate
    assert (
        standardized.input_seq_len.value
        == (native["config"]["spec"]["data"][0]["prompt_tokens"])
    )
    assert report.scenario.load.native.config == _as_report_schema_v1(native)["args"]


def test_pre_v0_7_model_name(tmp_path, native):
    """v0.6.x kept no ``args.model``, so the per-benchmark backend config is
    the only place the model name can be read from either format."""
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(_as_report_schema_v1(native)), encoding="utf-8")

    report = import_guidellm_v01(str(legacy))
    assert report.scenario.model.name == (native["config"]["spec"]["backend"]["model"])


# ---------------------------------------------------------------------------
# Profile -> rate/concurrency mapping, for the profiles this repo ships
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strategy", "expected"),
    [
        # Realized strategies as guidellm writes them, verified against real
        # runs of each profile kind against its mock-server.
        ({"type_": "constant", "rate": 2.0, "max_concurrency": 10240}, (2.0, None)),
        ({"type_": "poisson", "rate": 4.5, "max_concurrency": 10240}, (4.5, None)),
        (
            {"type_": "concurrent", "streams": 300, "max_concurrency": 300},
            (None, 300),
        ),
        (
            {"type_": "throughput", "max_concurrency": 8, "worker_count": 8},
            (None, 8),
        ),
        ({"type_": "synchronous", "worker_count": 1}, (None, 1)),
    ],
)
def test_rate_and_concurrency_per_strategy_kind(strategy, expected):
    """concurrent reports its bound as ``streams``, throughput as
    ``max_concurrency``, and synchronous carries neither -- the old converter
    read a single run-wide ``rate`` list for all of them."""
    results = {"config": {"strategy": strategy}}
    assert guidellm_native.rate_and_concurrency({}, results) == expected
