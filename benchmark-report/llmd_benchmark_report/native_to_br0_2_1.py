"""
Convert application native output formats into a Benchmark Report v0.2.1.

v0.2.1 is an additive superset of v0.2: it adds per-request payload-size and
per-modality multimodal statistics that inference-perf emits (PR #450 and
follow-ups). Everything else is identical to v0.2, so this module reuses the
v0.2 converters wholesale and only overrides :func:`import_inference_perf` to
fold in the multimodal block.

The only producer of multimodal fields today is inference-perf; the other
harness importers are re-exported unchanged. A v0.2 report they emit is, by
construction, a valid v0.2.1 report.
"""

import os

from .base import Units
from .core import import_yaml, load_benchmark_report, update_dict
from .schema_v0_2_1 import BenchmarkReportV021

# Re-export the v0.2 converters that v0.2.1 does not change, so the CLI can
# import the full set of importers from a single module per report version.
from .native_to_br0_2 import (  # noqa: F401
    import_inference_max,
    import_vllm_benchmark,
    import_guidellm,
    import_guidellm_all,
)
from .native_to_br0_2 import import_inference_perf as _import_inference_perf_v0_2
from .native_to_br0_2 import (
    import_inference_perf_session as _import_inference_perf_session_v0_2,
)


# Native (inference-perf) field name -> (schema field name, units) for each
# modality. The native report nests these under successes.{image,video,audio};
# see inference_perf/payloads/{image,video,audio}/metrics.py and
# tests/required/reportgen/test_lifecycle_report_shape.py upstream. The schema
# names properties rather than units, hence bytes->filesize, seconds->duration.
_MODALITY_FIELDS = {
    "image": [
        ("count", "count", Units.COUNT),
        ("pixels", "pixels", Units.PIXELS),
        ("bytes", "filesize", Units.BYTES),
        ("aspect_ratio", "aspect_ratio", Units.RATIO),
    ],
    "video": [
        ("count", "count", Units.COUNT),
        ("frames", "frames", Units.COUNT),
        ("pixels", "pixels", Units.PIXELS),
        ("bytes", "filesize", Units.BYTES),
        ("aspect_ratio", "aspect_ratio", Units.RATIO),
    ],
    "audio": [
        ("count", "count", Units.COUNT),
        ("seconds", "duration", Units.S),
        ("bytes", "filesize", Units.BYTES),
    ],
}

# Native throughput scalar -> (schema field name, units).
_MEDIA_RATE_FIELDS = [
    ("images_per_sec", "image_rate", Units.IMAGE_PER_S),
    ("videos_per_sec", "video_rate", Units.VIDEO_PER_S),
    ("audios_per_sec", "audio_rate", Units.AUDIO_PER_S),
]


def _stats(raw: dict | None, units: Units) -> dict | None:
    """Map an inference-perf summary dict to a schema Statistics dict.

    inference-perf reports percentiles as ``median``/``p0.1``/``p99.9``; the
    schema names them ``p50``/``p0p1``/``p99p9``. Returns None when the source
    summary is absent so the (Optional) schema field is simply omitted.
    """
    if not isinstance(raw, dict):
        return None
    return {
        "units": units,
        "mean": raw.get("mean"),
        "min": raw.get("min"),
        "p0p1": raw.get("p0.1"),
        "p1": raw.get("p1"),
        "p5": raw.get("p5"),
        "p10": raw.get("p10"),
        "p25": raw.get("p25"),
        "p50": raw.get("median"),
        "p75": raw.get("p75"),
        "p90": raw.get("p90"),
        "p95": raw.get("p95"),
        "p99": raw.get("p99"),
        "p99p9": raw.get("p99.9"),
        "max": raw.get("max"),
    }


def _rate(value: float | None, units: Units) -> dict | None:
    """Wrap a scalar per-second rate as a schema Statistics dict, or None."""
    if value is None:
        return None
    return {"units": units, "mean": value}


def _build_multimodal(successes: dict) -> dict:
    """Build the multimodal block from the successes section of the results.

    Only modalities actually present in the run are included, and within each
    only the sub-fields the harness reported.
    """
    multimodal = {}
    for modality, fields in _MODALITY_FIELDS.items():
        native = successes.get(modality)
        if not isinstance(native, dict):
            continue
        stats = {
            schema_name: _stats(native.get(native_name), units)
            for native_name, schema_name, units in fields
            if isinstance(native.get(native_name), dict)
        }
        if stats:
            multimodal[modality] = stats
    return multimodal


def import_inference_perf_session(results_file: str) -> BenchmarkReportV021:
    """Import an Inference Perf session lifecycle file as a BenchmarkReportV021.

    The v0.2 converter is reused wholesale: v0.2.1 adds nothing to session
    metrics. It exists so requesting v0.2.1 yields a v0.2.1 report rather than a
    v0.2 one, which cannot carry the treatment grouping at all.
    """
    br_dict = _import_inference_perf_session_v0_2(results_file).dump()
    br_dict["version"] = "0.2.1"

    metadata = _treatment_metadata()
    if metadata:
        br_dict.setdefault("scenario", {}).setdefault("load", {}).setdefault(
            "metadata", {}
        ).update(metadata)

    return BenchmarkReportV021(**br_dict)


def _treatment_metadata() -> dict:
    """Treatment grouping from the harness environment.

    v0.2.1-only: BenchmarkReportV02 forbids these fields, so the shared v0.2
    converter cannot populate them. Concurrent treatments share a stack, so a
    reader needs to know what else was running.
    """
    concurrent_with = [
        name.strip()
        for name in os.environ.get("LLMDBENCH_TREATMENT_CONCURRENT_WITH", "").split(",")
        if name.strip()
    ]
    metadata: dict = {}
    if os.environ.get("LLMDBENCH_TREATMENT_NAME"):
        metadata["treatment"] = os.environ["LLMDBENCH_TREATMENT_NAME"]
    if os.environ.get("LLMDBENCH_TREATMENT_GROUP"):
        metadata["treatment_group"] = os.environ["LLMDBENCH_TREATMENT_GROUP"]
    if concurrent_with:
        metadata["concurrent_with"] = concurrent_with
    return metadata


def import_inference_perf(results_file: str) -> BenchmarkReportV021:
    """Import data from an Inference Perf run as a BenchmarkReportV021.

    Delegates the v0.2 portion of the report to the v0.2 converter, then folds
    in the additive v0.2.1 fields (request_size, the per-modality multimodal
    block, and per-modality delivery rates) read from the same results file.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReportV021: Imported data.
    """
    # Reuse all the v0.2 logic (scenario, latency, token throughput, ...).
    br_dict = _import_inference_perf_v0_2(results_file).dump()
    br_dict["version"] = "0.2.1"

    metadata = _treatment_metadata()
    if metadata:
        br_dict.setdefault("scenario", {}).setdefault("load", {}).setdefault(
            "metadata", {}
        ).update(metadata)

    results = import_yaml(results_file)
    successes = results.get("successes")

    # Multimodal stats live under successes; when every request failed the v0.2
    # converter omits the successes-derived aggregate entirely, and so do we.
    if isinstance(successes, dict):
        requests_add = {}

        request_size = _stats(successes.get("request_size_bytes"), Units.BYTES)
        if request_size:
            requests_add["request_size"] = request_size

        multimodal = _build_multimodal(successes)
        if multimodal:
            requests_add["multimodal"] = multimodal

        throughput = successes.get("throughput", {})
        rates = {
            schema_name: _rate(throughput.get(native_name), units)
            for native_name, schema_name, units in _MEDIA_RATE_FIELDS
            if throughput.get(native_name) is not None
        }

        aggregate = {}
        if requests_add:
            aggregate["requests"] = requests_add
        if rates:
            aggregate["throughput"] = rates

        if aggregate:
            update_dict(
                br_dict,
                {"results": {"request_performance": {"aggregate": aggregate}}},
            )

    return load_benchmark_report(br_dict)
