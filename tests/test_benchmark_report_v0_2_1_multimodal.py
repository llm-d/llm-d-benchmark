"""Feature and unit-guardrail tests for benchmark report schema v0.2.1.

Complements the back-compat test: this exercises what v0.2.1 *adds*. A fully
populated multi-modal report validates and round-trips, every per-modality unit
is accepted with its correct category, and mismatched units are rejected,
including proof that the inherited v0.2 `request_rate` guardrail is not loosened
by the new media-throughput category.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llmdbenchmark.analysis.benchmark_report.schema_v0_2_1 import (
    AggregateRequests,
    AggregateThroughput,
    AudioPayloadStats,
    BenchmarkReportV021,
    ImagePayloadStats,
    VideoPayloadStats,
)

# Fields that are required independent of the one under test.
REQUIRED_FIELDS = {AggregateRequests: {"total": 0}}


def _stat(units: str, mean: float = 1.0) -> dict:
    return {"units": units, "mean": mean}


def _build(model, field: str, units: str):
    kwargs = dict(REQUIRED_FIELDS.get(model, {}))
    kwargs[field] = _stat(units)
    return model(**kwargs)


def test_full_multimodal_report_validates_and_roundtrips():
    report = BenchmarkReportV021(
        version="0.2.1",
        run={"uid": "u"},
        results={
            "request_performance": {
                "aggregate": {
                    "requests": {
                        "total": 100,
                        "request_size": _stat("bytes", 51234.0),
                        "multimodal": {
                            "image": {
                                "count": _stat("count", 2.0),
                                "pixels": _stat("pixels", 2073600.0),
                                "aspect_ratio": _stat("ratio", 1.78),
                                "filesize": _stat("bytes", 40000.0),
                            },
                            "video": {
                                "frames": _stat("count", 16.0),
                            },
                            "audio": {"duration": _stat("s", 10.0)},
                        },
                    },
                    "throughput": {
                        "request_rate": _stat("queries/s", 12.0),
                        "image_rate": _stat("images/s", 24.0),
                        "video_rate": _stat("videos/s", 3.0),
                        "audio_rate": _stat("audios/s", 2.0),
                    },
                }
            }
        },
    )
    mm = report.results.request_performance.aggregate.requests.multimodal
    assert mm.image.pixels.mean == 2073600.0
    assert mm.video.frames.mean == 16.0
    assert mm.audio.duration.mean == 10.0
    # Survives a dump -> reload cycle.
    assert BenchmarkReportV021(**report.dump()).version == "0.2.1"


@pytest.mark.parametrize(
    "model, field, units",
    [
        (AggregateThroughput, "image_rate", "images/s"),
        (AggregateThroughput, "video_rate", "videos/s"),
        (AggregateThroughput, "audio_rate", "audios/s"),
        (ImagePayloadStats, "count", "count"),
        (ImagePayloadStats, "pixels", "pixels"),
        (ImagePayloadStats, "aspect_ratio", "ratio"),
        (ImagePayloadStats, "filesize", "bytes"),
        (VideoPayloadStats, "frames", "count"),
        (AudioPayloadStats, "duration", "s"),
        (AggregateRequests, "request_size", "bytes"),
    ],
)
def test_correct_units_accepted(model, field, units):
    _build(model, field, units)


@pytest.mark.parametrize(
    "model, field, units",
    [
        # A media rate is not a request rate.
        (AggregateThroughput, "image_rate", "queries/s"),
        # The inherited v0.2 request_rate guardrail must stay intact.
        (AggregateThroughput, "request_rate", "images/s"),
        # An aspect ratio is a ratio, not a portion.
        (ImagePayloadStats, "aspect_ratio", "fraction"),
        (ImagePayloadStats, "pixels", "s"),
        (AudioPayloadStats, "duration", "bytes"),
        (AggregateRequests, "request_size", "queries/s"),
    ],
)
def test_mismatched_units_rejected(model, field, units):
    with pytest.raises(ValidationError):
        _build(model, field, units)
