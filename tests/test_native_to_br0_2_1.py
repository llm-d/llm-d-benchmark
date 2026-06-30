"""Converter test: inference-perf native report -> benchmark report v0.2.1.

The input fixture (tests/fixtures/inference_perf_lifecycle.yaml) is genuine
inference-perf output, captured from inference-perf's own summarize_requests
(see the fixture header). This test pins that the converter maps every
multimodal field to the right v0.2.1 location and units, performs the
native->schema renames (median->p50, p0.1->p0p1, p99.9->p99p9, *_per_sec->
*_rate, request_size_bytes->request_size, bytes->filesize, seconds->duration),
and preserves the v0.2 content it inherits from the reused v0.2 converter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.benchmark_report.base import Units
from llmdbenchmark.analysis.benchmark_report.native_to_br0_2_1 import (
    import_inference_perf,
)
from llmdbenchmark.analysis.benchmark_report.schema_v0_2_1 import BenchmarkReportV021

FIXTURE = Path(__file__).parent / "fixtures" / "inference_perf_lifecycle.yaml"


@pytest.fixture(scope="module")
def native() -> dict:
    with open(FIXTURE) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def report() -> BenchmarkReportV021:
    return import_inference_perf(str(FIXTURE))


def _assert_maps(stat, raw: dict, units: Units):
    """A converted Statistics must carry the right units and the native values,
    with inference-perf's median/p0.1/p99.9 renamed to p50/p0p1/p99p9."""
    assert stat.units == units
    assert stat.mean == raw["mean"]
    assert stat.min == raw["min"]
    assert stat.max == raw["max"]
    assert stat.p50 == raw["median"]
    assert stat.p0p1 == raw["p0.1"]
    assert stat.p99p9 == raw["p99.9"]


def test_returns_v0_2_1_report(report):
    assert isinstance(report, BenchmarkReportV021)
    assert report.version == "0.2.1"


def test_request_size_mapped(report, native):
    rs = report.results.request_performance.aggregate.requests.request_size
    _assert_maps(rs, native["successes"]["request_size_bytes"], Units.BYTES)


def test_image_stats_mapped(report, native):
    img = report.results.request_performance.aggregate.requests.multimodal.image
    raw = native["successes"]["image"]
    _assert_maps(img.count, raw["count"], Units.COUNT)
    _assert_maps(img.pixels, raw["pixels"], Units.PIXELS)
    _assert_maps(img.filesize, raw["bytes"], Units.BYTES)
    _assert_maps(img.aspect_ratio, raw["aspect_ratio"], Units.RATIO)


def test_video_stats_mapped(report, native):
    vid = report.results.request_performance.aggregate.requests.multimodal.video
    raw = native["successes"]["video"]
    _assert_maps(vid.count, raw["count"], Units.COUNT)
    _assert_maps(vid.frames, raw["frames"], Units.COUNT)
    _assert_maps(vid.pixels, raw["pixels"], Units.PIXELS)
    _assert_maps(vid.filesize, raw["bytes"], Units.BYTES)
    _assert_maps(vid.aspect_ratio, raw["aspect_ratio"], Units.RATIO)
    # inference-perf does not emit a video duration, so the schema must not
    # invent one.
    assert not hasattr(vid, "duration")


def test_audio_stats_mapped(report, native):
    aud = report.results.request_performance.aggregate.requests.multimodal.audio
    raw = native["successes"]["audio"]
    _assert_maps(aud.count, raw["count"], Units.COUNT)
    _assert_maps(aud.duration, raw["seconds"], Units.S)
    _assert_maps(aud.filesize, raw["bytes"], Units.BYTES)


def test_media_rates_mapped(report, native):
    tp = report.results.request_performance.aggregate.throughput
    raw = native["successes"]["throughput"]
    assert tp.image_rate.units == Units.IMAGE_PER_S
    assert tp.image_rate.mean == raw["images_per_sec"]
    assert tp.video_rate.units == Units.VIDEO_PER_S
    assert tp.video_rate.mean == raw["videos_per_sec"]
    assert tp.audio_rate.units == Units.AUDIO_PER_S
    assert tp.audio_rate.mean == raw["audios_per_sec"]


def test_inherited_v0_2_content_preserved(report, native):
    """Reusing the v0.2 converter must not drop the v0.2 fields."""
    agg = report.results.request_performance.aggregate
    # Request counts and the v0.2 request_rate / token rates survive.
    assert agg.requests.total == native["successes"]["count"]
    assert (
        agg.throughput.request_rate.mean
        == native["successes"]["throughput"]["requests_per_sec"]
    )
    assert (
        agg.throughput.output_token_rate.mean
        == (native["successes"]["throughput"]["output_tokens_per_sec"])
    )
    # A v0.2 latency block is still populated.
    assert agg.latency.request_latency.mean is not None


def test_roundtrips(report):
    assert BenchmarkReportV021(**report.dump()).version == "0.2.1"
