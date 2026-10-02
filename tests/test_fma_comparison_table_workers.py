"""fma_comparison_table must not scale inference-perf totals by num_workers.

inference-perf feeds every worker into one request collector and writes one
summary_lifecycle_metrics.json for the whole run, so its counts and
throughputs already cover all workers.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "llmdbenchmark/analysis/scripts/fma_comparison_table.py"
)

SUMMARY = {
    "benchmark_time_seconds": 60,
    "load_summary": {"count": 100},
    "successes": {
        "count": 98,
        "throughput": {
            "requests_per_sec": 1.6,
            "input_tokens_per_sec": 400,
            "output_tokens_per_sec": 200,
        },
    },
    "failures": {"count": 2},
}


def _arm(root: Path) -> Path:
    results = root / "run-20261002" / "results"
    results.mkdir(parents=True)
    (results / "summary_lifecycle_metrics.json").write_text(json.dumps(SUMMARY))
    (results / "profile.yaml").write_text("load:\n  num_workers: 2\n")
    return root


def test_totals_are_not_multiplied_by_num_workers(tmp_path):
    arms = [_arm(tmp_path / name) for name in ("baseline", "warm", "hot")]
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--baseline-dir",
            str(arms[0]),
            "--warmstart-dir",
            str(arms[1]),
            "--hotstart-dir",
            str(arms[2]),
            "--col-baseline",
            "B",
            "--col-warmstart",
            "W",
            "--col-hotstart",
            "H",
            "--workload",
            "profile.yaml",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = {
        line.split("|")[1].strip(): line.split("|")[2].strip()
        for line in out.splitlines()
        if line.startswith("| ")
    }
    assert rows["Total requests"] == "100"
    assert rows["Successes"] == "98"
    assert rows["Failures"] == "2"
    assert rows["Throughput (req/s)"] == "1.6"
    assert "- **Total requests:** 100" in out
