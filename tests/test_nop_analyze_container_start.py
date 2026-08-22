"""Tests for the Container Start line in write_vllm_metrics.

write_vllm_metrics prints the per-instance vLLM timing block. It must emit a
Container Start(secs) line from the container_start metric alongside the
existing Pod Start(secs) line, so the container-start anchor is visible in the
analysis output next to the pod-start anchor.
"""

import importlib.util
import io
import os
import sys

_ANALYSIS = os.path.join("llmdbenchmark", "analysis")
_SCRIPTS = os.path.join(_ANALYSIS, "scripts")
for _p in (_ANALYSIS, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SPEC = importlib.util.spec_from_file_location(
    "nop_analyze_results",
    os.path.join(_SCRIPTS, "nop-analyze_results.py"),
)
nar = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nar)


def _scalar(value):
    return {"value": value}


def _metrics_metadata(pod_start, container_start):
    return {
        "name": "vllm-standalone-qwen-qwen3-4b",
        "pod_start": _scalar(pod_start),
        "container_start": _scalar(container_start),
        "vllm_start_timestamp": _scalar(0.0),
        "vllm_ready_timestamp": _scalar(0.0),
        "load": {"time": _scalar(0.0), "transfer_rate": _scalar(0.0)},
        "dynamo_bytecode_transform": _scalar(0.0),
        "torch_compile": _scalar(0.0),
        "memory_profiling": {
            "initial_free": _scalar(0.0),
            "after_free": _scalar(0.0),
            "time": _scalar(0.0),
        },
    }


def test_writes_container_start_line():
    out = io.StringIO()
    nar.write_vllm_metrics(out, [_metrics_metadata(30.0, 12.5)], 0)
    text = out.getvalue()
    assert "Container Start(secs)" in text
    assert "12.500" in text


def test_still_writes_pod_start_line():
    out = io.StringIO()
    nar.write_vllm_metrics(out, [_metrics_metadata(30.0, 12.5)], 0)
    text = out.getvalue()
    assert "Pod  Start(secs)" in text
    assert "30.000" in text
