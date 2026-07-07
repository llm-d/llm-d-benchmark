"""Tests for run-only inference-perf report conversion."""

from __future__ import annotations

from pathlib import Path

import yaml

from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
    _get_harness_meta,
    import_inference_perf,
)

FIXTURE = Path(__file__).parent / "fixtures" / "inference_perf_lifecycle.yaml"


def test_run_only_conversion_uses_metadata_without_kubernetes_context(
    tmp_path: Path, monkeypatch
) -> None:
    results_file = tmp_path / "stage_0_lifecycle_metrics.json"
    results_file.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")

    workload_file = tmp_path / "code_generation.yaml"
    workload_file.write_text(
        yaml.safe_dump(
            {
                "load": {"type": "concurrent"},
                "api": {"type": "completion", "streaming": True},
                "server": {
                    "type": "vllm",
                    "model_name": "Qwen/Qwen3-32B",
                    "base_url": "http://10.128.0.22",
                },
                "data": {
                    "type": "conversation_replay",
                    "conversation_replay": {"seed": 42, "num_conversations": 20},
                },
                "metrics": {
                    "type": "prometheus",
                    "prometheus": {"google_managed": True, "scrape_interval": 15},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "run_metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "harness_args": f"--config_file {workload_file}",
                "harness_start": "2026-06-23T22:54:25+00:00",
                "harness_stop": "2026-06-23T23:14:35+00:00",
                "harness_delta": "PT1210S",
                "harness_version": "test-version",
                "harness_name": "inference-perf",
                "harness_workload": workload_file.name,
                "harness_rc": "0",
                "model": "Qwen/Qwen3-32B",
                "endpoint_url": "http://10.128.0.22",
                "namespace": "llm-d-storage",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("LLMDBENCH_MAGIC_ENVAR", "harness_pod")
    monkeypatch.setenv("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", str(tmp_path))
    monkeypatch.delenv("LLMDBENCH_BASE64_CONTEXT_CONTENTS", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT", raising=False)
    if hasattr(_get_harness_meta, "_cache"):
        delattr(_get_harness_meta, "_cache")

    report = import_inference_perf(str(results_file))

    assert report.run.user == "namespace=llm-d-storage"
    assert report.scenario.load.native.config["metrics"]["prometheus"]["google_managed"]
