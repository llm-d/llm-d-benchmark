"""collect_metrics.sh scrapes the harness's own /metrics when the launcher exports its port.

inference-perf-llm-d-benchmark.sh exports LLMDBENCH_HARNESS_METRICS_PORT only when
the installed inference-perf serves the endpoint, so the variable is the whole gate.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


COLLECT_METRICS = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "collect_metrics.sh"
)


# Runs one collect_metrics_snapshot with no vLLM or EPP pods in the cluster and a fake
# curl that logs each URL and answers "inference_perf_requests_in_flight 3".
# Returns the URLs curl was called with.
def _snapshot(tmp_path: Path, extra_env: dict[str, str]) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_log = tmp_path / "curl.log"
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "${{@: -1}}" >> "{curl_log}"\n'
        "echo 'inference_perf_requests_in_flight 3'\n"
    )
    fake_curl.chmod(0o755)
    fake_kubectl = bin_dir / "kubectl"
    fake_kubectl.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_kubectl.chmod(0o755)
    functions = tmp_path / "collect_metrics_functions.sh"
    functions.write_text(
        COLLECT_METRICS.read_text().split("# Main command dispatcher")[0]
    )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "KUBECTL_CMD": str(fake_kubectl),
        "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR": str(tmp_path),
        "HOSTNAME": "harness-pod",
    }
    env.pop("LLMDBENCH_HARNESS_METRICS_PORT", None)
    env.update(extra_env)
    subprocess.run(
        [
            "bash",
            "-c",
            f"source {functions}; init_metrics_dir; collect_metrics_snapshot",
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    return curl_log.read_text().splitlines() if curl_log.exists() else []


# Port 9464 exported -> curl hits http://127.0.0.1:9464/metrics and the raw file
# harness-pod_<ts>_metrics.log carries "# Source: harness_prometheus_metrics" and the sample.
def test_exported_port_scrapes_the_harness(tmp_path: Path) -> None:
    urls = _snapshot(tmp_path, {"LLMDBENCH_HARNESS_METRICS_PORT": "9464"})

    assert urls == ["http://127.0.0.1:9464/metrics"]
    [raw] = (tmp_path / "metrics" / "raw").glob("harness-pod_*_metrics.log")
    text = raw.read_text()
    assert "# Pod: harness-pod" in text
    assert "# Source: harness_prometheus_metrics" in text
    assert "inference_perf_requests_in_flight 3" in text


# No port exported (other harnesses, older images) -> curl never called, no harness file.
def test_no_port_skips_the_harness(tmp_path: Path) -> None:
    assert _snapshot(tmp_path, {}) == []
    assert not list((tmp_path / "metrics" / "raw").glob("harness-pod_*"))
