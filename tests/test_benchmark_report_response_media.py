"""Response media statistics in Benchmark Report v0.2 (llm-d/llm-d-benchmark#2127).

`requests.output_multimodal` and `throughput.output_{image,video,audio}_rate`
describe the media the server returned, reusing the request-side payload
classes. `VideoPayloadStats` gains `duration` on both sides. Everything is
optional, so a report without these fields still validates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import (
    AggregateRequests,
    AggregateThroughput,
    BenchmarkReportV02,
    VideoPayloadStats,
)

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "benchmark-report"
    / "llmd_benchmark_report"
    / "br_v0_2_example.yaml"
)


def _stat(units: str, mean: float = 1.0) -> dict:
    return {"units": units, "mean": mean}


def _report(requests: dict, throughput: dict | None = None) -> BenchmarkReportV02:
    aggregate: dict = {"requests": {"total": 10, **requests}}
    if throughput is not None:
        aggregate["throughput"] = throughput
    return BenchmarkReportV02(
        version="0.2.1",
        run={"uid": "u"},
        results={"request_performance": {"aggregate": aggregate}},
    )


def test_response_media_report_validates_and_roundtrips():
    report = _report(
        {
            "multimodal": {"image": {"count": _stat("count", 1.0)}},
            "output_multimodal": {
                "image": {
                    "count": _stat("count", 4.0),
                    "filesize": _stat("bytes", 350000.0),
                    "pixels": _stat("pixels", 1048576.0),
                    "aspect_ratio": _stat("ratio", 1.0),
                },
                "video": {
                    "count": _stat("count", 1.0),
                    "frames": _stat("count", 121.0),
                    "duration": _stat("s", 5.04),
                },
                "audio": {"duration": _stat("s", 12.5)},
            },
        },
        {
            "output_image_rate": _stat("images/s", 1.5),
            "output_video_rate": _stat("videos/s", 0.4),
            "output_audio_rate": _stat("audios/s", 2.0),
        },
    )
    aggregate = report.results.request_performance.aggregate
    requests = aggregate.requests
    assert requests.multimodal.image.count.mean == 1.0
    assert requests.output_multimodal.image.count.mean == 4.0
    assert requests.output_multimodal.video.duration.mean == 5.04
    assert requests.output_multimodal.audio.duration.mean == 12.5
    assert aggregate.throughput.output_image_rate.mean == 1.5
    assert aggregate.throughput.output_video_rate.mean == 0.4
    assert aggregate.throughput.output_audio_rate.mean == 2.0
    reloaded = BenchmarkReportV02(**report.dump()).results.request_performance.aggregate
    assert reloaded.requests.output_multimodal.video.duration.mean == 5.04
    assert reloaded.throughput.output_video_rate.units == "videos/s"


def test_request_side_duration_on_video():
    report = _report({"multimodal": {"video": {"duration": _stat("ms", 4000.0)}}})
    video = report.results.request_performance.aggregate.requests.multimodal.video
    assert video.duration.mean == 4000.0


def test_report_without_response_media_still_validates():
    report = _report(
        {"multimodal": {"image": {"count": _stat("count", 2.0)}}},
        {"image_rate": _stat("images/s", 24.0)},
    )
    aggregate = report.results.request_performance.aggregate
    assert aggregate.requests.output_multimodal is None
    assert aggregate.throughput.output_image_rate is None
    assert aggregate.throughput.output_video_rate is None
    assert aggregate.throughput.output_audio_rate is None
    dumped = report.dump()["results"]["request_performance"]["aggregate"]
    assert "output_multimodal" not in dumped["requests"]


@pytest.mark.parametrize(
    "field, units",
    [
        ("output_image_rate", "images/s"),
        ("output_video_rate", "videos/s"),
        ("output_audio_rate", "audios/s"),
    ],
)
def test_output_rate_units_accepted(field, units):
    AggregateThroughput(**{field: _stat(units)})


@pytest.mark.parametrize(
    "field, units",
    [
        ("output_image_rate", "queries/s"),
        ("output_video_rate", "tokens/s"),
        ("output_audio_rate", "s"),
    ],
)
def test_output_rate_wrong_units_rejected(field, units):
    with pytest.raises(ValidationError, match=f'Invalid units "{units}" for "{field}"'):
        AggregateThroughput(**{field: _stat(units)})


@pytest.mark.parametrize("units", ["s", "ms"])
def test_video_duration_time_units_accepted(units):
    VideoPayloadStats(duration=_stat(units))


@pytest.mark.parametrize("units", ["bytes", "count", "videos/s"])
def test_video_duration_wrong_units_rejected(units):
    with pytest.raises(
        ValidationError, match=f'Invalid units "{units}" for "duration"'
    ):
        VideoPayloadStats(duration=_stat(units))


def test_output_multimodal_wrong_units_rejected():
    with pytest.raises(ValidationError, match='Invalid units "count" for "duration"'):
        AggregateRequests(
            total=1, output_multimodal={"video": {"duration": _stat("count")}}
        )


def test_output_multimodal_unknown_modality_rejected():
    with pytest.raises(ValidationError) as excinfo:
        AggregateRequests(
            total=1, output_multimodal={"text": {"count": _stat("count")}}
        )
    assert [e["loc"] for e in excinfo.value.errors()] == [("output_multimodal", "text")]


def test_example_report_carries_response_media():
    report = BenchmarkReportV02(**yaml.safe_load(EXAMPLE.read_text()))
    aggregate = report.results.request_performance.aggregate
    assert aggregate.requests.output_multimodal.video.duration.units == "s"
    assert aggregate.throughput.output_video_rate.units == "videos/s"
