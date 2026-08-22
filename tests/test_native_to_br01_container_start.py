"""Tests that container_start survives the native -> benchmark-report v0.1 import.

The native results dict carries a bare container_start float on each vLLM
metric (elapsed seconds from the main container's start to Pod-Ready), next to
the pod_start anchor. import_nop hand-maps each vLLM metric into the
{units, value} schema, so it must map container_start too or the anchor is
silently dropped from the emitted report. The mapping must also degrade
gracefully on older native artifacts that predate the field.
"""

import yaml

from llmdbenchmark.analysis.benchmark_report.native_to_br0_1 import import_nop


def _nop_results(include_container_start=True):
    """Minimal nop results dict carrying one vLLM metric."""
    vllm_metric = {
        "name": "vllm-standalone-x",
        "pod_start": 125.0,
        "vllm_start_timestamp": 1000.0,
        "vllm_ready_timestamp": 1079.0,
        "load": {"time": 1.0, "size": 1.0, "transfer_rate": 1.0},
        "dynamo_bytecode_transform": 0.0,
        "torch_compile": 0.0,
        "memory_profiling": {"initial_free": 0.0, "after_free": 0.0, "time": 0.0},
        "sleep_wake": [],
    }
    if include_container_start:
        vllm_metric["container_start"] = 119.0
    return {
        "scenario": {
            "model": {"name": "m"},
            "deploy_methods": "standalone",
            "load_format": "auto",
            "sleep_mode": "0",
            "gpus": 1,
            "platform": {
                "engines": [
                    {"name": "vllm", "version": "0.1", "args": {}, "image": "img:tag"}
                ]
            },
        },
        "time": {"duration": 1.0, "start": 0.0, "stop": 1.0},
        "vllm_metrics": [vllm_metric],
        "extra_metrics": [],
    }


def _vllm_metadata(tmp_path, results):
    path = tmp_path / "results.yaml"
    path.write_text(yaml.safe_dump(results))
    br = import_nop(str(path))
    bd = br.model_dump()
    md = next(m for m in bd["metrics"]["metadata"] if m["name"] == "vllm_metrics")
    return md["value"][0]


def test_container_start_survives(tmp_path):
    m = _vllm_metadata(tmp_path, _nop_results(include_container_start=True))
    assert m["container_start"]["value"] == 119.0
    assert m["container_start"]["units"] == "s"


def test_pod_start_still_survives(tmp_path):
    m = _vllm_metadata(tmp_path, _nop_results(include_container_start=True))
    assert m["pod_start"]["value"] == 125.0


def test_absent_container_start_defaults_to_zero(tmp_path):
    # Older native artifact: no container_start on the vLLM metric.
    m = _vllm_metadata(tmp_path, _nop_results(include_container_start=False))
    assert m["container_start"]["value"] == 0.0
