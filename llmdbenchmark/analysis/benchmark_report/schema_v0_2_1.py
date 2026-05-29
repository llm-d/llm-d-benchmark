"""
Benchmark report v0.2.1

Additive minor revision of v0.2 that adds optional multi-modal payload
statistics (image / video / audio) to the request aggregates.

Every field introduced here is Optional, so any document valid under v0.2 is
also valid under v0.2.1. v0.2 is imported and extended in place rather than
copied, so the unchanged majority of the schema keeps a single definition and
this file contains only the multi-modal delta plus the containment shims needed
to thread the extended aggregates up to a new report root.

Scope note: this revision covers the results side only (the per-modality stats
the client can derive from the payloads it sent, mirroring the fields emitted by
inference-perf's lifecycle report). A standardized load-side `multimodal`
descriptor on LoadStandardized is deliberately left out of this revision; see
the PR description.
"""

from pydantic import BaseModel, model_validator

from .base import (
    UNITS_MEDIA_THROUGHPUT,
    UNITS_MEMORY,
    UNITS_QUANTITY,
    UNITS_RATIO,
    UNITS_TIME,
)
from .schema_v0_2 import (
    MODEL_CONFIG,
    VERSION as VERSION_V02,
    AggregateRequestPerformance as AggregateRequestPerformanceV02,
    AggregateRequests as AggregateRequestsV02,
    AggregateThroughput as AggregateThroughputV02,
    BenchmarkReportV02,
    RequestPerformance as RequestPerformanceV02,
    Results as ResultsV02,
    Run,
    Scenario,
    Statistics,
)

# BenchmarkReport schema version
VERSION = "0.2.1"

# v0.2.1 is a strict additive superset of v0.2; this guards against a future
# v0.2 bump silently drifting out from under the version we extend.
assert VERSION_V02 == "0.2", (
    f"schema_v0_2_1 expects to extend v0.2, found {VERSION_V02}"
)


###############################################################################
# Per-modality payload statistics
#
# Single-inheritance hierarchy so that fields shared across modalities are
# declared exactly once:
#
#   MediaPayloadStats        count, bytes                 (all modalities)
#     └─ VisualPayloadStats  + pixels, aspect_ratio       (image, video)
#         ├─ ImagePayloadStats
#         └─ VideoPayloadStats  + frames, seconds
#     └─ AudioPayloadStats   + seconds
#
# Adding a modality is a new leaf class plus one field on MultiModalRequests.
###############################################################################


class MediaPayloadStats(BaseModel):
    """Payload statistics shared by every media modality.

    All fields are distributions over the individual media instances the client
    sent, derived purely from the request payload.
    """

    model_config = MODEL_CONFIG.copy()

    count: Statistics | None = None
    """Number of media instances of this modality per request."""
    bytes: Statistics | None = None
    """Encoded size per media instance."""

    @model_validator(mode="after")
    def check_media_units(self):
        if self.count and self.count.units not in UNITS_QUANTITY:
            raise ValueError(
                f'Invalid units "{self.count.units}", must be one of:'
                f" {' '.join(UNITS_QUANTITY)}"
            )
        if self.bytes and self.bytes.units not in UNITS_MEMORY:
            raise ValueError(
                f'Invalid units "{self.bytes.units}", must be one of:'
                f" {' '.join(UNITS_MEMORY)}"
            )
        return self


class VisualPayloadStats(MediaPayloadStats):
    """Payload statistics common to pixel-based modalities (image and video)."""

    model_config = MODEL_CONFIG.copy()

    pixels: Statistics | None = None
    """Pixel count per media instance (height x width, summed over frames)."""
    aspect_ratio: Statistics | None = None
    """Aspect ratio (width / height) per media instance."""

    @model_validator(mode="after")
    def check_visual_units(self):
        if self.pixels and self.pixels.units not in UNITS_QUANTITY:
            raise ValueError(
                f'Invalid units "{self.pixels.units}", must be one of:'
                f" {' '.join(UNITS_QUANTITY)}"
            )
        if self.aspect_ratio and self.aspect_ratio.units not in UNITS_RATIO:
            raise ValueError(
                f'Invalid units "{self.aspect_ratio.units}", must be one of:'
                f" {' '.join(UNITS_RATIO)}"
            )
        return self


class ImagePayloadStats(VisualPayloadStats):
    """Image payload statistics."""

    model_config = MODEL_CONFIG.copy()


class VideoPayloadStats(VisualPayloadStats):
    """Video payload statistics."""

    model_config = MODEL_CONFIG.copy()

    frames: Statistics | None = None
    """Number of frames per video instance."""
    seconds: Statistics | None = None
    """Duration per video instance."""

    @model_validator(mode="after")
    def check_video_units(self):
        if self.frames and self.frames.units not in UNITS_QUANTITY:
            raise ValueError(
                f'Invalid units "{self.frames.units}", must be one of:'
                f" {' '.join(UNITS_QUANTITY)}"
            )
        if self.seconds and self.seconds.units not in UNITS_TIME:
            raise ValueError(
                f'Invalid units "{self.seconds.units}", must be one of:'
                f" {' '.join(UNITS_TIME)}"
            )
        return self


class AudioPayloadStats(MediaPayloadStats):
    """Audio payload statistics."""

    model_config = MODEL_CONFIG.copy()

    seconds: Statistics | None = None
    """Duration per audio instance."""

    @model_validator(mode="after")
    def check_audio_units(self):
        if self.seconds and self.seconds.units not in UNITS_TIME:
            raise ValueError(
                f'Invalid units "{self.seconds.units}", must be one of:'
                f" {' '.join(UNITS_TIME)}"
            )
        return self


class MultiModalRequests(BaseModel):
    """Per-modality request payload statistics for multi-modal workloads."""

    model_config = MODEL_CONFIG.copy()

    image: ImagePayloadStats | None = None
    """Image payload statistics."""
    video: VideoPayloadStats | None = None
    """Video payload statistics."""
    audio: AudioPayloadStats | None = None
    """Audio payload statistics."""


###############################################################################
# Extended request aggregates
###############################################################################


class AggregateRequests(AggregateRequestsV02):
    """v0.2 request statistics, plus multi-modal payload details."""

    model_config = MODEL_CONFIG.copy()

    request_size: Statistics | None = None
    """Total encoded request size, including all media payloads."""
    multimodal: MultiModalRequests | None = None
    """Per-modality payload statistics."""

    @model_validator(mode="after")
    def check_request_size_units(self):
        if self.request_size and self.request_size.units not in UNITS_MEMORY:
            raise ValueError(
                f'Invalid units "{self.request_size.units}", must be one of:'
                f" {' '.join(UNITS_MEMORY)}"
            )
        return self


class AggregateThroughput(AggregateThroughputV02):
    """v0.2 throughput metrics, plus per-modality payload rates."""

    model_config = MODEL_CONFIG.copy()

    image_rate: Statistics | None = None
    """Image delivery rate."""
    video_rate: Statistics | None = None
    """Video delivery rate."""
    audio_rate: Statistics | None = None
    """Audio delivery rate."""

    @model_validator(mode="after")
    def check_media_rate_units(self):
        for name, stat in (
            ("image_rate", self.image_rate),
            ("video_rate", self.video_rate),
            ("audio_rate", self.audio_rate),
        ):
            if stat and stat.units not in UNITS_MEDIA_THROUGHPUT:
                raise ValueError(
                    f'Invalid units "{stat.units}" for {name}, must be one of:'
                    f" {' '.join(UNITS_MEDIA_THROUGHPUT)}"
                )
        return self


###############################################################################
# Containment shims: re-thread the extended aggregates up to a new report root.
# Each class redeclares only the field whose type changed; all other fields are
# inherited from the v0.2 definition.
###############################################################################


class AggregateRequestPerformance(AggregateRequestPerformanceV02):
    """Aggregate performance metrics (v0.2.1 aggregates)."""

    model_config = MODEL_CONFIG.copy()

    requests: AggregateRequests | None = None
    """Aggregate request details."""
    throughput: AggregateThroughput | None = None
    """Aggregate response throughput performance metrics."""


class RequestPerformance(RequestPerformanceV02):
    """Request-level performance metrics (v0.2.1 aggregates)."""

    model_config = MODEL_CONFIG.copy()

    aggregate: AggregateRequestPerformance | None = None
    """Aggregate performance metrics."""


class Results(ResultsV02):
    """Benchmark results (v0.2.1 request performance)."""

    model_config = MODEL_CONFIG.copy()

    request_performance: RequestPerformance | None = None
    """Request-level performance metrics."""


class BenchmarkReportV021(BenchmarkReportV02):
    """Benchmark report v0.2.1."""

    model_config = MODEL_CONFIG.copy()
    model_config["title"] = "Benchmark Report v0.2.1"

    version: str = VERSION
    """Version of the schema."""
    run: Run
    """Benchmark run details."""
    scenario: Scenario | None = None
    """Stack configuration and workload details of experiment."""
    results: Results
    """Experiment results."""
