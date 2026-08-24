from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml


HELPER = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "inference-perf-lifecycle.sh"
)


def _capture(tmp_path: Path, message: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$HELPER"; printf \'%s\\n\' "$MESSAGE" | '
            'inference_perf_capture_stream "$RESULTS" stderr.log 2 experiment-1',
        ],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "HELPER": str(HELPER),
            "MESSAGE": message,
            "RESULTS": str(tmp_path),
        },
    )


def test_final_successful_stage_emits_event_and_marker(tmp_path: Path) -> None:
    result = _capture(tmp_path, "2026-08-13 INFO Stage 2 - run completed")

    marker = yaml.safe_load((tmp_path / "traffic_complete.yaml").read_text())
    assert marker["schema_version"] == "1"
    assert marker["event"] == "traffic_complete"
    assert marker["experiment_id"] == "experiment-1"
    assert marker["final_stage_id"] == 2
    assert "LLMDBENCH_EVENT_V1 traffic_complete" in result.stdout
    assert "Stage 2 - run completed" in (tmp_path / "stderr.log").read_text()


def test_nonfinal_or_failed_stage_does_not_emit_event(tmp_path: Path) -> None:
    _capture(tmp_path, "Stage 1 - run completed")
    _capture(tmp_path, "Stage 2 - run failed")

    assert not (tmp_path / "traffic_complete.yaml").exists()
