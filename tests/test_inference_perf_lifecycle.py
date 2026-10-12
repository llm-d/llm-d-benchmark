from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


HELPER = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "inference-perf-lifecycle.sh"
)
WRAPPER = HELPER.with_name("inference-perf-llm-d-benchmark.sh")
PROFILES = HELPER.parent.parent / "profiles" / "inference-perf"


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


@pytest.mark.parametrize(
    ("workload", "delay", "extra_body"),
    [
        ("sanity_random.yaml.in", "", {}),
        (
            "speculative_prefill_multi_turn.yaml.in",
            "20",
            {"temperature": 0.0, "seed": 42, "top_p": 1.0},
        ),
    ],
)
def test_wrapper_renders_profile_and_metadata(
    tmp_path: Path, workload: str, delay: str, extra_body: dict[str, float | int]
) -> None:
    results = tmp_path / "results"
    env = {
        **os.environ,
        "LLMDBENCH_RUN_WORKSPACE_DIR": str(PROFILES.parent.parent),
        "LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME": workload,
        "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR": str(results),
    }
    env.pop("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", None)
    env.pop("LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY", None)
    setup = WRAPPER.read_text().split("export LLMDBENCH_HARNESS_ARGS=", 1)[0]
    result = subprocess.run(
        [
            "bash",
            "-e",
            "-c",
            setup
            + '\npython -c \'import json, os; print(json.dumps([os.getenv("LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS", ""), os.environ["LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY"]]))\'\n',
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    actual_delay, actual_extra_body = json.loads(result.stdout.splitlines()[-1])
    assert actual_delay == delay
    assert json.loads(actual_extra_body) == extra_body

    source = yaml.safe_load((PROFILES / workload).read_text())
    source.pop("llmdbenchmark", None)
    source["storage"]["local_storage"]["path"] = str(results)
    assert yaml.safe_load((results / workload).read_text()) == source


def test_wrapper_preserves_explicit_chat_overrides(tmp_path: Path) -> None:
    results = tmp_path / "results"
    env = {
        **os.environ,
        "LLMDBENCH_RUN_WORKSPACE_DIR": str(PROFILES.parent.parent),
        "LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME": "speculative_prefill_multi_turn.yaml.in",
        "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR": str(results),
        "LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS": "3",
        "LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY": '{"seed": 7}',
    }
    setup = WRAPPER.read_text().split("export LLMDBENCH_HARNESS_ARGS=", 1)[0]
    result = subprocess.run(
        [
            "bash",
            "-e",
            "-c",
            setup
            + '\nprintf "%s\\n" "$LLMDBENCH_INFERENCE_PERF_INTER_TURN_DELAY_SECONDS" "$LLMDBENCH_INFERENCE_PERF_CHAT_EXTRA_BODY"\n',
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert result.stdout.splitlines()[-2:] == ["3", '{"seed": 7}']
