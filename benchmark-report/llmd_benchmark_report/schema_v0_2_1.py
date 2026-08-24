"""
Benchmark report v0.2.1

Additive minor revision of v0.2 that adds:

  - optional multi-modal payload statistics (image / video / audio) to the
    request aggregates;
  - optional engine and router serving signals to the observability time
    series (scheduler queue depth, prefix-cache effectiveness, token counters,
    router pool state), which v0.2 covers for hardware metrics only.

Every field introduced here is Optional, so any document valid under v0.2 is
also valid under v0.2.1. v0.2 is imported and extended in place rather than
copied, so the unchanged majority of the schema keeps a single definition and
this file contains only the deltas plus the containment shims needed to thread
the extended models up to a new report root.

Scope note: this revision covers the results side only (the per-modality stats
the client can derive from the payloads it sent, mirroring the fields emitted by
inference-perf's lifecycle report). A standardized load-side `multimodal`
descriptor on LoadStandardized is deliberately left out of this revision; see
the PR description.
"""

from typing import ClassVar

from pydantic import BaseModel

from .base import (
    UNITS_MEDIA_THROUGHPUT,
    UNITS_MEMORY,
    UNITS_PORTION,
    UNITS_QUANTITY,
    UNITS_RATIO,
    UNITS_TIME,
    Units,
    UnitsValidatedModel,
)
from .schema_v0_2 import (
    MODEL_CONFIG,
    VERSION as VERSION_V02,
    AggregateRequestPerformance as AggregateRequestPerformanceV02,
    AggregateRequests as AggregateRequestsV02,
    AggregateThroughput as AggregateThroughputV02,
    BenchmarkReportV02,
    ComponentObservability as ComponentObservabilityV02,
    Observability as ObservabilityV02,
    RequestPerformance as RequestPerformanceV02,
    Results as ResultsV02,
    Run,
    Scenario,
    Statistics,
    TimeSeriesData,
    TimeSeriesResourceMetrics as TimeSeriesResourceMetricsV02,
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
#   MediaPayloadStats        count, filesize              (all modalities)
#     └─ VisualPayloadStats  + pixels, aspect_ratio       (image, video)
#         ├─ ImagePayloadStats
#         └─ VideoPayloadStats  + frames
#     └─ AudioPayloadStats   + duration
#
# Adding a modality is a new leaf class plus one field on MultiModalRequests.
###############################################################################


class MediaPayloadStats(UnitsValidatedModel):
    """Payload statistics shared by every media modality.

    All fields are distributions over the individual media instances the client
    sent, derived purely from the request payload.
    """

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {
        "count": UNITS_QUANTITY,
        "filesize": UNITS_MEMORY,
    }

    count: Statistics | None = None
    """Number of media instances of this modality per request."""
    filesize: Statistics | None = None
    """Encoded size per media instance."""


class VisualPayloadStats(MediaPayloadStats):
    """Payload statistics common to pixel-based modalities (image and video)."""

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {
        "pixels": UNITS_QUANTITY,
        "aspect_ratio": UNITS_RATIO,
    }

    pixels: Statistics | None = None
    """Pixel count per media instance (height x width, summed over frames)."""
    aspect_ratio: Statistics | None = None
    """Aspect ratio (width / height) per media instance."""


class ImagePayloadStats(VisualPayloadStats):
    """Image payload statistics."""

    model_config = MODEL_CONFIG.copy()


class VideoPayloadStats(VisualPayloadStats):
    """Video payload statistics."""

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {"frames": UNITS_QUANTITY}

    frames: Statistics | None = None
    """Number of frames per video instance."""


class AudioPayloadStats(MediaPayloadStats):
    """Audio payload statistics."""

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {"duration": UNITS_TIME}

    duration: Statistics | None = None
    """Duration per audio instance."""


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


class AggregateRequests(AggregateRequestsV02, UnitsValidatedModel):
    """v0.2 request statistics, plus multi-modal payload details.

    Inherits the v0.2 input/output-length unit checks and adds a declarative
    rule for the new request_size field.
    """

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {"request_size": UNITS_MEMORY}

    request_size: Statistics | None = None
    """Total encoded request size, including all media payloads."""
    multimodal: MultiModalRequests | None = None
    """Per-modality payload statistics."""


class AggregateThroughput(AggregateThroughputV02, UnitsValidatedModel):
    """v0.2 throughput metrics, plus per-modality payload rates."""

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {
        "image_rate": UNITS_MEDIA_THROUGHPUT,
        "video_rate": UNITS_MEDIA_THROUGHPUT,
        "audio_rate": UNITS_MEDIA_THROUGHPUT,
    }

    image_rate: Statistics | None = None
    """Image delivery rate."""
    video_rate: Statistics | None = None
    """Video delivery rate."""
    audio_rate: Statistics | None = None
    """Audio delivery rate."""


###############################################################################
# Extended time-series resource metrics
#
# v0.2 covers hardware utilization only. These add the engine-level serving
# signals that are actually scrapeable from a vLLM/EPP stack: scheduler queue
# depth, prefix-cache effectiveness, token counters, and router pool state.
###############################################################################


class TimeSeriesResourceMetrics(TimeSeriesResourceMetricsV02, UnitsValidatedModel):
    """v0.2 hardware time series, plus engine and router serving signals.

    Inherits the v0.2 hardware unit checks and adds declarative rules for the
    new fields. Counter-derived rates are percent, not fraction, because
    compute_ratio_series emits num/den*100.
    """

    model_config = MODEL_CONFIG.copy()

    UNIT_RULES: ClassVar[dict[str, list[Units]]] = {
        "num_requests_running": UNITS_QUANTITY,
        "num_requests_waiting": UNITS_QUANTITY,
        "num_preemptions": UNITS_QUANTITY,
        "prefix_cache_queries": UNITS_QUANTITY,
        "prefix_cache_hits": UNITS_QUANTITY,
        "prefix_cache_hit_rate": UNITS_PORTION,
        "external_prefix_cache_queries": UNITS_QUANTITY,
        "external_prefix_cache_hits": UNITS_QUANTITY,
        "external_prefix_cache_hit_rate": UNITS_PORTION,
        "prompt_tokens": UNITS_QUANTITY,
        "generation_tokens": UNITS_QUANTITY,
        "pool_avg_kv_cache_utilization": UNITS_PORTION,
        "pool_avg_queue_size": UNITS_QUANTITY,
        "pool_avg_running_requests": UNITS_QUANTITY,
        "pool_ready_pods": UNITS_QUANTITY,
    }

    num_requests_running: TimeSeriesData | None = None
    """Requests actively decoding on the engine over time."""
    num_requests_waiting: TimeSeriesData | None = None
    """Requests queued ahead of the engine over time."""
    num_preemptions: TimeSeriesData | None = None
    """Cumulative scheduler preemptions over time."""
    prefix_cache_queries: TimeSeriesData | None = None
    """Cumulative tokens looked up in the local prefix cache."""
    prefix_cache_hits: TimeSeriesData | None = None
    """Cumulative tokens served from the local prefix cache."""
    prefix_cache_hit_rate: TimeSeriesData | None = None
    """Local prefix cache hit rate over time."""
    external_prefix_cache_queries: TimeSeriesData | None = None
    """Cumulative tokens looked up in the external (offloaded) prefix cache."""
    external_prefix_cache_hits: TimeSeriesData | None = None
    """Cumulative tokens served from the external prefix cache."""
    external_prefix_cache_hit_rate: TimeSeriesData | None = None
    """External prefix cache hit rate over time."""
    prompt_tokens: TimeSeriesData | None = None
    """Cumulative prompt tokens processed over time."""
    generation_tokens: TimeSeriesData | None = None
    """Cumulative generated tokens over time."""
    pool_avg_kv_cache_utilization: TimeSeriesData | None = None
    """Router view of mean KV cache utilization across the pool."""
    pool_avg_queue_size: TimeSeriesData | None = None
    """Router view of mean queue depth across the pool."""
    pool_avg_running_requests: TimeSeriesData | None = None
    """Router view of mean running requests across the pool."""
    pool_ready_pods: TimeSeriesData | None = None
    """Endpoints the router considers ready over time."""


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


class ComponentObservability(ComponentObservabilityV02):
    """Observability metrics for a component (v0.2.1 time series)."""

    model_config = MODEL_CONFIG.copy()

    time_series: TimeSeriesResourceMetrics | None = None
    """Time series resource metrics."""


class Observability(ObservabilityV02):
    """Observability metrics (v0.2.1 time series)."""

    model_config = MODEL_CONFIG.copy()
    model_config["extra"] = "allow"

    components: list[ComponentObservability] | None = None
    """Per-component observability metrics."""


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
    observability: Observability | None = None
    """Observability metrics (v0.2.1 time series)."""


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
