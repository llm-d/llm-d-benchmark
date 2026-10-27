"""A pod that is not Ready yet is recorded with a null ready_timestamp.

collect_metrics.sh records every Running serving pod, including one still
loading its model. pod_startup_times.json is embedded in the v0.2 report as
observability.pod_startup_times, whose ready_timestamp is a datetime or null.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import PodStartupTimes

COLLECT_METRICS = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "collect_metrics.sh"
)


def _pod(name: str, ready: bool) -> dict:
    conditions = [
        {
            "type": "Ready",
            "status": "True" if ready else "False",
            "lastTransitionTime": "2026-10-01T00:02:00Z",
        }
    ]
    return {
        "metadata": {
            "name": name,
            "creationTimestamp": "2026-10-01T00:00:00Z",
            "labels": {"llm-d.ai/model": "m", "llm-d.ai/role": "decode"},
        },
        "spec": {"nodeName": "node-1"},
        "status": {"phase": "Running", "conditions": conditions},
    }


def test_not_ready_pod_has_null_ready_timestamp(tmp_path: Path) -> None:
    payload = tmp_path / "pods.json"
    payload.write_text(
        json.dumps({"items": [_pod("m-decode-a", True), _pod("m-decode-b", False)]})
    )
    fake_kubectl = tmp_path / "kubectl"
    fake_kubectl.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$*" == *inferenceServing=true* ]]; then cat {payload}; '
        "else echo '{\"items\":[]}'; fi\n"
    )
    fake_kubectl.chmod(0o755)
    functions = tmp_path / "collect_metrics_functions.sh"
    functions.write_text(
        COLLECT_METRICS.read_text().split("# Main command dispatcher")[0]
    )

    subprocess.run(
        ["bash", "-c", f"source {functions}; collect_pod_startup_times"],
        env={
            **os.environ,
            "KUBECTL_CMD": str(fake_kubectl),
            "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR": str(tmp_path),
        },
        check=True,
        capture_output=True,
    )

    data = json.loads(
        (tmp_path / "metrics" / "processed" / "pod_startup_times.json").read_text()
    )
    pods = {p["name"]: p for p in data["pods"]}
    assert pods["m-decode-a"]["ready_timestamp"] == "2026-10-01T00:02:00Z"
    assert pods["m-decode-a"]["startup_seconds"] == 120.0
    assert pods["m-decode-b"]["ready_timestamp"] is None
    PodStartupTimes(**data)
