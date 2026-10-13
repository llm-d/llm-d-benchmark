"""Tests for the eval-containers run-level aggregator."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.aggregate_eval_containers import (
    CSV_NAME,
    DONE_MARKER,
    REPORT_NAME,
    generate_agentic_summary,
    main,
    _polyglot_language,
)
from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
    is_agentic_request_span,
)


def _span(name: str, start_ns: int, end_ns: int, attrs: dict) -> dict:
    return {
        "name": name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [
            {"key": k, "value": v if isinstance(v, dict) else {"intValue": str(v)}}
            for k, v in attrs.items()
        ],
    }


def _write_task(
    root: Path,
    name: str,
    *,
    task_id: int,
    benchmark: str = "aider-polyglot",
    reward: float | None = 1.0,
    exit_code: str | None = "0",
    spans: list[dict] | None = None,
    delta: str | None = "PT120S",
    reward_txt: str | None = None,
    harness_start: str | None = None,
) -> Path:
    task_dir = root / name
    (task_dir / "task").mkdir(parents=True)
    (task_dir / "agent").mkdir(parents=True)

    if reward is not None:
        (task_dir / "task" / "result.json").write_text(
            json.dumps(
                {
                    "task_id": str(task_id),
                    "benchmark": benchmark,
                    "reward": reward,
                    "passed": reward >= 1.0,
                }
            ),
            encoding="utf-8",
        )
    if reward_txt is not None:
        verifier = task_dir / "logs" / "verifier"
        verifier.mkdir(parents=True)
        # No trailing newline, matching what the grader writes.
        (verifier / "reward.txt").write_text(reward_txt, encoding="utf-8")

    metadata = f"harness_name: eval-containers\nharness_rc: 0\ntask_id: {task_id}\nmodel: test-model\n"
    if delta:
        metadata += f'harness_delta: "{delta}"\n'
    if harness_start:
        metadata += f'harness_start: "{harness_start}"\n'
    (task_dir / "run_metadata.yaml").write_text(metadata, encoding="utf-8")

    if exit_code is not None:
        # Deliberately no trailing newline: the real files have none.
        (task_dir / "agent" / ".exit-code").write_text(exit_code, encoding="utf-8")

    if spans:
        doc = {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}
        (task_dir / "traces.jsonl").write_text(json.dumps(doc) + "\n", encoding="utf-8")
    return task_dir


@pytest.mark.parametrize(
    "name,expected",
    [
        ("/openai/v1/responses", True),
        ("/openai/v1/chat/completions", True),
        ("/anthropic/v1/messages", True),
        # The Gemini wire, which gemini-cli uses; both forms must match.
        ("/genai/v1beta/models/m:generateContent", True),
        ("/genai/v1beta/models/m:streamGenerateContent?alt=sse", True),
        ("litellm_request", True),
        # The provider span carries usage, and must NOT be counted as a request.
        ("chat Qwen/Qwen3.6-27B", False),
        ("plugin.telemetry.prehook", False),
        ("key.selection", False),
        ("dock-gateway", False),
    ],
)
def test_request_span_detection(name: str, expected: bool) -> None:
    assert is_agentic_request_span(name) is expected


def test_polyglot_language_bounds() -> None:
    # Boundary ids on both sides of each language's range.
    assert _polyglot_language(0) == "cpp"
    assert _polyglot_language(25) == "cpp"
    assert _polyglot_language(26) == "go"
    assert _polyglot_language(64) == "go"
    assert _polyglot_language(65) == "java"
    assert _polyglot_language(111) == "java"
    assert _polyglot_language(112) == "javascript"
    assert _polyglot_language(160) == "javascript"
    assert _polyglot_language(161) == "python"
    assert _polyglot_language(194) == "python"
    assert _polyglot_language(195) == "rust"
    assert _polyglot_language(224) == "rust"
    assert _polyglot_language(None) == ""


def test_empty_dir_returns_zero(tmp_path: Path) -> None:
    assert generate_agentic_summary(tmp_path) == 0
    assert generate_agentic_summary(tmp_path / "missing") == 0


def test_tokens_come_from_provider_span_not_http_span(tmp_path: Path) -> None:
    """Usage is on "chat <model>"; the HTTP span defines the call count."""
    spans = [
        _span("/openai/v1/responses", 1_000_000_000, 3_000_000_000, {}),
        _span(
            "chat test-model",
            1_100_000_000,
            2_900_000_000,
            {
                "gen_ai.usage.input_tokens": 1000,
                "gen_ai.usage.output_tokens": 200,
                # Ends in "output_tokens" but must NOT be added to the total.
                "gen_ai.usage.reasoning.output_tokens": 77,
            },
        ),
    ]
    _write_task(tmp_path, "run_1", task_id=200, spans=spans)

    assert generate_agentic_summary(tmp_path) == 1
    report = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())
    obs = report["results"]["observability"]
    assert obs["eval_containers_input_tokens"] == 1000
    assert obs["eval_containers_output_tokens"] == 200  # not 277
    assert obs["eval_containers_total_tokens"] == 1200
    # Two spans, one logical call.
    assert obs["eval_containers_llm_calls"] == 1


def test_score_and_language_aggregation(tmp_path: Path) -> None:
    rust = [
        _span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {}),
    ]
    _write_task(tmp_path, "run_1", task_id=200, reward=1.0, spans=rust)
    _write_task(tmp_path, "run_2", task_id=201, reward=0.0, spans=rust)
    _write_task(tmp_path, "run_3", task_id=170, reward=1.0, spans=rust)

    assert generate_agentic_summary(tmp_path) == 3
    obs = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]

    assert obs["eval_containers_tasks"] == 3
    assert obs["eval_containers_passed_count"] == 2
    assert obs["eval_containers_pass_rate"] == pytest.approx(2 / 3)
    assert obs["eval_containers_reward_sum"] == pytest.approx(2.0)
    # Per-language split: rust 1/2, python 1/1.
    assert obs["eval_containers_pass_rate_rust"] == pytest.approx(0.5)
    assert obs["eval_containers_tasks_rust"] == 2
    assert obs["eval_containers_pass_rate_python"] == pytest.approx(1.0)


def test_reward_txt_fallback_when_result_json_missing(tmp_path: Path) -> None:
    _write_task(tmp_path, "run_1", task_id=5, reward=None, reward_txt="1.0")
    assert generate_agentic_summary(tmp_path) == 1
    obs = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    assert obs["eval_containers_passed_count"] == 1
    assert obs["eval_containers_reward_sum"] == pytest.approx(1.0)


def test_exit_code_classification(tmp_path: Path) -> None:
    _write_task(tmp_path, "run_1", task_id=1, exit_code="0")
    _write_task(tmp_path, "run_2", task_id=2, exit_code="124")
    _write_task(tmp_path, "run_3", task_id=3, exit_code="1")

    generate_agentic_summary(tmp_path)
    obs = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    assert obs["eval_containers_tasks_exit_timeout"] == 1
    assert obs["eval_containers_tasks_exit_error"] == 1


def test_ttft_discards_values_exceeding_their_call(tmp_path: Path) -> None:
    """A TTFT longer than its own call cannot be real and must not be averaged."""
    spans = [
        _span(
            "/openai/v1/responses",
            0,
            1_000_000_000,
            {
                "gen_ai.response.total_latency_ms": 1000,
                # 0.5s inside a 1s call: valid.
                "gen_ai.response.time_to_first_token": 500_000,
            },
        ),
        _span(
            "/openai/v1/responses",
            2_000_000_000,
            3_000_000_000,
            {
                "gen_ai.response.total_latency_ms": 1000,
                # 2838s inside a 1s call: impossible, discard.
                "gen_ai.response.time_to_first_token": 2_838_000_000,
            },
        ),
    ]
    _write_task(tmp_path, "run_1", task_id=1, spans=spans)

    generate_agentic_summary(tmp_path)
    obs = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    assert obs["eval_containers_ttft_valid_calls"] == 1
    assert obs["eval_containers_ttft_discarded_calls"] == 1
    assert obs["eval_containers_ttft_seconds"]["mean"] == pytest.approx(0.5)


def test_task_latency_uses_harness_wall_clock(tmp_path: Path) -> None:
    spans = [_span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {})]
    _write_task(tmp_path, "run_1", task_id=1, spans=spans, delta="PT300S")

    generate_agentic_summary(tmp_path)
    obs = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    # Wall-clock (300s) is reported separately from the LLM-span proxy (1s).
    assert obs["eval_containers_task_latency_seconds"]["mean"] == pytest.approx(300.0)
    assert obs["eval_containers_task_llm_span_seconds"]["mean"] == pytest.approx(1.0)


def test_rates_use_the_wall_clock_of_parallel_tasks(tmp_path: Path) -> None:
    # Four tasks run side by side in their own pods, 100 s each, 10 calls of
    # 1,000 tokens apiece: 40 calls and 40,000 tokens in 100 s of wall clock.
    call = _span(
        "chat test-model",
        1_000_000_000,
        2_000_000_000,
        {"gen_ai.usage.input_tokens": 900, "gen_ai.usage.output_tokens": 100},
    )
    request = _span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {})
    for i in range(4):
        _write_task(
            tmp_path,
            f"run_{i + 1}",
            task_id=i,
            spans=[request, call] * 10,
            delta="PT100S",
            harness_start="2026-07-14T00:00:00Z",
        )

    generate_agentic_summary(tmp_path)
    throughput = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["request_performance"]["aggregate"]["throughput"]
    assert throughput["request_rate"]["mean"] == pytest.approx(0.4)
    assert throughput["total_token_rate"]["mean"] == pytest.approx(400.0)


def test_missing_timestamps_omit_task_latency(tmp_path: Path) -> None:
    """Runs predating the harness timestamps must not fabricate a task latency."""
    spans = [_span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {})]
    _write_task(tmp_path, "run_1", task_id=1, spans=spans, delta=None)

    generate_agentic_summary(tmp_path)
    obs = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    assert "eval_containers_task_latency_seconds" not in obs
    assert obs["eval_containers_task_llm_span_seconds"]["mean"] == pytest.approx(1.0)


def test_csv_has_units_in_column_names_and_one_row_per_task(tmp_path: Path) -> None:
    spans = [_span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {})]
    _write_task(tmp_path, "run_1", task_id=200, spans=spans)
    _write_task(tmp_path, "run_2", task_id=170, spans=spans)

    generate_agentic_summary(tmp_path)
    with open(tmp_path / "run_summary" / CSV_NAME, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 2
    # Sorted by task id.
    assert [r["task_id"] for r in rows] == ["170", "200"]
    assert rows[0]["language"] == "python"
    assert rows[1]["language"] == "rust"
    # Latency columns state their unit.
    header = rows[0].keys()
    assert "call_latency_mean_ms" in header
    assert "task_latency_s" in header


def test_report_validates_against_v0_2_schema(tmp_path: Path) -> None:
    """The emitted YAML must load as a real v0.2 report, not just be YAML."""
    from llmdbenchmark.analysis.benchmark_report.core import load_benchmark_report

    spans = [
        _span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {}),
        _span(
            "chat test-model",
            1_000_000_000,
            2_000_000_000,
            {"gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5},
        ),
    ]
    _write_task(tmp_path, "run_1", task_id=1, spans=spans)
    generate_agentic_summary(tmp_path)

    data = yaml.safe_load((tmp_path / "run_summary" / REPORT_NAME).read_text())
    report = load_benchmark_report(data)
    assert report.version == "0.2.1"
    assert report.run.uid == "run"


def test_time_to_first_call_anchors_on_harness_start(tmp_path: Path) -> None:
    """It must measure harness_start -> first call, not task_latency - llm_span.

    Anchoring on the LAST call's end forces the answer to equal
    (task_latency - task_llm_span), i.e. the trailing grading time, which is a
    different quantity wearing this column's name.
    """
    # harness starts at t=0; the only call runs from t=+10s to t=+12s.
    start = "2026-08-12T00:00:00+00:00"
    base_ns = 1786492800 * 10**9  # 2026-08-12T00:00:00Z
    spans = [
        _span("/openai/v1/responses", base_ns + 10 * 10**9, base_ns + 12 * 10**9, {}),
    ]
    # 100s total: 10s before the call, 2s of call, 88s of grading after.
    _write_task(
        tmp_path, "run_1", task_id=1, spans=spans, delta="PT100S", harness_start=start
    )
    generate_agentic_summary(tmp_path)
    with open(tmp_path / "run_summary" / CSV_NAME, encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    # The real answer is 10s. The buggy derivation would give 100 - 2 = 98.
    assert float(row["time_to_first_call_s"]) == pytest.approx(10.0, abs=0.01)
    assert float(row["task_latency_s"]) == pytest.approx(100.0)
    assert float(row["task_llm_span_s"]) == pytest.approx(2.0)


def test_time_to_first_call_blank_without_harness_start(tmp_path: Path) -> None:
    """Runs predating the timestamps must blank it, not fabricate it."""
    spans = [_span("/openai/v1/responses", 1_000_000_000, 2_000_000_000, {})]
    _write_task(tmp_path, "run_1", task_id=1, spans=spans, delta="PT100S")
    generate_agentic_summary(tmp_path)
    with open(tmp_path / "run_summary" / CSV_NAME, encoding="utf-8") as f:
        row = list(csv.DictReader(f))[0]
    assert row["time_to_first_call_s"] == ""


# ---------------------------------------------------------------------------
# In-pod entry point
# ---------------------------------------------------------------------------


def _done(task_dir: Path) -> Path:
    (task_dir / DONE_MARKER).write_text("0\n", encoding="utf-8")
    return task_dir


def test_main_writes_the_summary_as_its_own_result_set(tmp_path: Path) -> None:
    _done(_write_task(tmp_path, "exp_1", task_id=1))
    _done(_write_task(tmp_path, "exp_2", task_id=2, reward=0.0))
    _write_task(tmp_path, "other_1", task_id=3)

    assert main(["--expect", "2", "--deadline", "5", str(tmp_path), "exp"]) == 0

    summary = tmp_path / "exp_summary"
    assert sorted(p.name for p in summary.iterdir()) == sorted([CSV_NAME, REPORT_NAME])
    data = yaml.safe_load((summary / REPORT_NAME).read_text())
    assert data["run"]["uid"] == "exp"
    obs = data["results"]["observability"]
    assert obs["eval_containers_tasks"] == 2
    assert obs["eval_containers_tasks_expected"] == 2
    assert obs["eval_containers_passed_count"] == 1
    # Nothing staged is left behind.
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_main_waits_for_every_marker(tmp_path: Path, monkeypatch) -> None:
    import llmdbenchmark.analysis.aggregate_eval_containers as agg

    _done(_write_task(tmp_path, "exp_1", task_id=1))
    late = _write_task(tmp_path, "exp_2", task_id=2)
    sleeps: list[float] = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        _done(late)

    monkeypatch.setattr(agg.time, "sleep", _sleep)
    assert main(["--expect", "2", "--deadline", "0", str(tmp_path), "exp"]) == 0

    assert len(sleeps) == 1
    data = yaml.safe_load((tmp_path / "exp_summary" / REPORT_NAME).read_text())
    assert data["results"]["observability"]["eval_containers_tasks"] == 2


def test_main_summarizes_what_finished_by_the_deadline(
    tmp_path: Path, monkeypatch
) -> None:
    import llmdbenchmark.analysis.aggregate_eval_containers as agg

    _done(_write_task(tmp_path, "exp_1", task_id=1))
    # Still running: has metadata but no marker, so it must not be read.
    _write_task(tmp_path, "exp_2", task_id=2)
    clock = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(agg.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(agg.time, "sleep", lambda _s: None)

    assert main(["--expect", "2", "--deadline", "10", str(tmp_path), "exp"]) == 0

    obs = yaml.safe_load((tmp_path / "exp_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    assert obs["eval_containers_tasks"] == 1
    assert obs["eval_containers_tasks_expected"] == 2


def test_main_never_fails_and_leaves_nothing_half_done(
    tmp_path: Path, monkeypatch
) -> None:
    import llmdbenchmark.analysis.aggregate_eval_containers as agg

    _done(_write_task(tmp_path, "exp_1", task_id=1))

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(agg, "_write_csv", _boom)
    assert main(["--expect", "1", "--deadline", "5", str(tmp_path), "exp"]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["exp_1"]


def test_summary_dir_is_not_read_as_a_task(tmp_path: Path) -> None:
    _done(_write_task(tmp_path, "exp_1", task_id=1))
    assert main(["--expect", "1", "--deadline", "5", str(tmp_path), "exp"]) == 0
    # A second pass, as on a retried attempt, still sees exactly one task.
    assert main(["--expect", "1", "--deadline", "5", str(tmp_path), "exp"]) == 0
    obs = yaml.safe_load((tmp_path / "exp_summary" / REPORT_NAME).read_text())[
        "results"
    ]["observability"]
    assert obs["eval_containers_tasks"] == 1


def test_done_marker_matches_the_harness_sentinel() -> None:
    from llmdbenchmark.utilities.kube_helpers import HARNESS_DONE_SENTINEL

    assert DONE_MARKER == HARNESS_DONE_SENTINEL


def test_runs_on_the_image_python() -> None:
    """The harness image is older than the driver's Python."""
    import ast

    import llmdbenchmark.analysis.aggregate_eval_containers as agg

    ast.parse(Path(agg.__file__).read_text(encoding="utf-8"), feature_version=(3, 12))
