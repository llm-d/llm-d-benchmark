"""Run-level aggregation for the eval-containers agentic harness.

The framework runs ONE benchmark task per parallel harness pod, so a 50-task
scenario leaves 50 result directories each holding a single-task benchmark
report. Nothing rolls those up: ``cross_treatment.py`` treats every directory as
a separate experimental *treatment* (its intended meaning -- one row per
configuration, compared side by side), which for this harness produces N rows
that look like N configurations of one task each. It also carries no
``observability`` columns, so the score cannot appear there at all.

This module answers the question a run actually poses: across all N tasks, what
was the score, the latency, and the token usage? It reads only artifacts that
already exist and writes two files under ``agentic-summary/``:

* ``agentic_run_report.yaml`` -- one v0.2-shaped benchmark report for the whole
  run, so existing tooling can read it.
* ``agentic_tasks.csv`` -- one row per task, with units in the column names.

Deliberately NOT written as ``benchmark_report_v0.2*.yaml`` at the results root:
``cross_treatment`` discovers inputs by that glob, and a run-level report sitting
there would be re-ingested as if it were another task.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.analysis.benchmark_report.base import Units
from llmdbenchmark.analysis.benchmark_report.core import load_benchmark_report
from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
    agentic_stat,
    is_agentic_request_span,
)

# aider-polyglot ships language-ordered, so a task id implies its language. Used
# to report per-language pass rates: a contiguous slice of this dataset is
# effectively single-language, and mixing languages without splitting them hides
# that (e.g. a python-heavy wave scoring lower than a javascript-heavy one is a
# task-mix effect, not a regression). Upper bounds inclusive.
_POLYGLOT_LANGUAGE_BOUNDS: tuple[tuple[int, str], ...] = (
    (25, "cpp"),
    (64, "go"),
    (111, "java"),
    (160, "javascript"),
    (194, "python"),
    (224, "rust"),
)

_CSV_COLUMNS = [
    "task_id",
    "benchmark",
    "language",
    "passed",
    "reward",
    "exit_code",
    "harness_rc",
    "llm_calls",
    "call_errors",
    "retries",
    "call_latency_mean_ms",
    "call_latency_p50_ms",
    "call_latency_max_ms",
    "task_latency_s",
    "task_llm_span_s",
    "time_to_first_call_s",
    "input_tokens",
    "output_tokens",
    "total_tokens",
]


def _log(context: Any, message: str) -> None:
    if context is not None and getattr(context, "logger", None) is not None:
        context.logger.log_info(message)


def _polyglot_language(task_id: int | None) -> str:
    if task_id is None:
        return ""
    for upper, name in _POLYGLOT_LANGUAGE_BOUNDS:
        if task_id <= upper:
            return name
    return ""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_yaml(path: Path) -> dict:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _read_exit_code(task_dir: Path) -> int | None:
    """Read ``agent/.exit-code``.

    Two-attempt images rewrite this file to attempt 2's status and preserve
    attempt 1 as ``.exit-code-1``; the retry wrapper resets it to 0 when the
    first attempt was clean. Whatever it ends up holding is the value the
    dashboard reads, so that is the value reported here. Note the file has no
    trailing newline, so it must be read per-file -- concatenating several
    yields one run-together string.
    """
    path = task_dir / "agent" / ".exit-code"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_reward(task_dir: Path) -> tuple[float | None, bool | None, str, int | None]:
    """Return ``(reward, passed, benchmark, task_id)`` for one task.

    ``task/result.json`` is authoritative. Fall back to
    ``logs/verifier/reward.txt`` (the grader writes it before the result file, so
    it survives a task that died between the two).
    """
    result = _read_json(task_dir / "task" / "result.json")
    benchmark = str(result.get("benchmark") or "")

    task_id = result.get("task_id")
    if task_id is None:
        task_id = _read_yaml(task_dir / "run_metadata.yaml").get("task_id")
    try:
        task_id_int = int(task_id)
    except (TypeError, ValueError):
        task_id_int = None

    if "reward" in result:
        reward = result.get("reward")
        passed = result.get("passed")
        try:
            reward_f = float(reward)
        except (TypeError, ValueError):
            reward_f = None
        if passed is None and reward_f is not None:
            passed = reward_f >= 1.0
        return reward_f, passed, benchmark, task_id_int

    try:
        text = (task_dir / "logs" / "verifier" / "reward.txt").read_text(
            encoding="utf-8"
        )
        reward_f = float(text.strip())
    except (OSError, ValueError):
        return None, None, benchmark, task_id_int
    return reward_f, reward_f >= 1.0, benchmark, task_id_int


def _iter_call_spans(traces: Path):
    """Yield ``(name, attributes, start_ns, end_ns)`` for every span in a file."""
    try:
        lines = traces.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        for resource_spans in doc.get("resourceSpans", []):
            for scope_spans in resource_spans.get("scopeSpans", []):
                for span in scope_spans.get("spans", []):
                    attrs = {
                        a.get("key", ""): a.get("value", {})
                        for a in span.get("attributes", [])
                    }
                    yield (
                        span.get("name", ""),
                        attrs,
                        int(span.get("startTimeUnixNano", 0) or 0),
                        int(span.get("endTimeUnixNano", 0) or 0),
                    )


def _int_attr(attrs: dict, key: str) -> int:
    value = attrs.get(key)
    if not value:
        return 0
    return int(value.get("intValue") or 0)


class _TaskMetrics:
    """Per-task metrics gathered from one result directory."""

    def __init__(self, task_dir: Path):
        self.name = task_dir.name
        reward, passed, benchmark, task_id = _read_reward(task_dir)
        self.reward = reward
        self.passed = passed
        self.benchmark = benchmark
        self.task_id = task_id
        self.exit_code = _read_exit_code(task_dir)

        metadata = _read_yaml(task_dir / "run_metadata.yaml")
        self.harness_rc = metadata.get("harness_rc")
        self.model = str(metadata.get("model") or "")
        self.task_latency_s = _duration_seconds(metadata.get("harness_delta"))
        self.harness_start_ns = _iso_to_unix_ns(metadata.get("harness_start"))

        self.call_latencies_ms: list[float] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.call_errors = 0
        self.retries = 0
        self.ttft_values_s: list[float] = []
        self.ttft_discarded = 0

        first_start: int | None = None
        last_end: int | None = None
        for name, attrs, start_ns, end_ns in _iter_call_spans(
            task_dir / "traces.jsonl"
        ):
            # Usage rides on the provider span, not the HTTP span.
            if "gen_ai.usage.input_tokens" in attrs:
                self.input_tokens += _int_attr(attrs, "gen_ai.usage.input_tokens")
                self.output_tokens += _int_attr(attrs, "gen_ai.usage.output_tokens")
            if "gen_ai.error" in attrs or "gen_ai.error.type" in attrs:
                self.call_errors += 1
            self.retries += _int_attr(attrs, "gen_ai.number_of_retries")

            latency_ms = _latency_ms(attrs, start_ns, end_ns)
            if latency_ms is not None:
                self._record_ttft(attrs, latency_ms)

            if not is_agentic_request_span(name):
                continue
            if start_ns and end_ns and end_ns > start_ns:
                self.call_latencies_ms.append((end_ns - start_ns) / 1e6)
                first_start = (
                    start_ns if first_start is None else min(first_start, start_ns)
                )
                last_end = end_ns if last_end is None else max(last_end, end_ns)

        self.llm_calls = len(self.call_latencies_ms)
        self.first_call_start_ns = first_start
        self.last_call_end_ns = last_end
        self.task_llm_span_s = (
            (last_end - first_start) / 1e9
            if first_start is not None
            and last_end is not None
            and last_end > first_start
            else None
        )

    def _record_ttft(self, attrs: dict, latency_ms: float) -> None:
        """Collect time-to-first-token, discarding values that cannot be real.

        The raw attribute's unit is not dependable: on a 54-task GAIA run its
        maximum implied 2,838 s inside an 823 s call. Rather than trust a
        documented scale factor, interpret it as microseconds and keep only
        values that fit inside their own call. A discarded count travels with
        the metric so a thin sample is visible instead of silently averaged.
        """
        raw = attrs.get("gen_ai.response.time_to_first_token")
        if not raw:
            return
        micros = raw.get("intValue") or raw.get("doubleValue")
        if micros is None:
            return
        seconds = float(micros) / 1e6
        if 0.0 <= seconds <= latency_ms / 1000.0:
            self.ttft_values_s.append(seconds)
        else:
            self.ttft_discarded += 1


def _latency_ms(attrs: dict, start_ns: int, end_ns: int) -> float | None:
    reported = attrs.get("gen_ai.response.total_latency_ms")
    if reported:
        value = reported.get("intValue") or reported.get("doubleValue")
        if value is not None:
            return float(value)
    if start_ns and end_ns and end_ns > start_ns:
        return (end_ns - start_ns) / 1e6
    return None


def _iso_to_unix_ns(value: Any) -> int | None:
    """Parse the harness's ISO-8601 ``harness_start`` into unix nanoseconds.

    Needed to anchor time-to-first-call: the span clock is unix-epoch
    nanoseconds, so the task's own start has to be expressed in the same units.
    Returns None on anything unparseable, so the metric is blanked rather than
    guessed.
    """
    if value is None:
        return None
    text = str(value).strip().strip('"')
    if not text:
        return None
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(text)
    except (ValueError, ImportError):
        return None
    if dt.tzinfo is None:
        # The harness writes an offset; a naive value would silently be read as
        # local time and skew the delta by hours.
        return None
    return int(dt.timestamp() * 1e9)


def _duration_seconds(delta: Any) -> float | None:
    """Parse an ISO-8601 ``PT<n>S`` duration, the form the harnesses write."""
    if delta is None:
        return None
    text = str(delta).strip().strip('"')
    if text.startswith("PT") and text.endswith("S"):
        text = text[2:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _task_dirs(results_dir: Path) -> list[Path]:
    return sorted(
        d
        for d in results_dir.iterdir()
        if d.is_dir()
        and d.name != "agentic-summary"
        and d.name != "cross-treatment-comparison"
        and (d / "run_metadata.yaml").exists()
    )


def _run_uid(tasks: list[_TaskMetrics]) -> str:
    """Derive a run id from the task directory names.

    Pods are named ``<experiment-id>_<parallel-index>``, so stripping the index
    yields the experiment they shared. Falls back to the first directory name if
    the pattern does not hold.
    """
    for task in tasks:
        base, sep, tail = task.name.rpartition("_")
        if sep and tail.isdigit():
            return base
    return tasks[0].name if tasks else "eval-containers"


def _build_report(tasks: list[_TaskMetrics]) -> dict:
    """Assemble the run-level v0.2-shaped report."""
    latencies_ms = [ms for t in tasks for ms in t.call_latencies_ms]
    task_latencies_s = [t.task_latency_s for t in tasks if t.task_latency_s is not None]
    llm_spans_s = [t.task_llm_span_s for t in tasks if t.task_llm_span_s is not None]
    ttft_s = [v for t in tasks for v in t.ttft_values_s]

    rewards = [t.reward for t in tasks if t.reward is not None]
    passed_count = sum(1 for t in tasks if t.passed)
    scored = len(rewards)

    input_tokens = sum(t.input_tokens for t in tasks)
    output_tokens = sum(t.output_tokens for t in tasks)
    llm_calls = sum(t.llm_calls for t in tasks)

    # Prefer real wall-clock for throughput; fall back to the LLM-span proxy so a
    # run predating the timestamps still reports a rate.
    wall_s = sum(task_latencies_s) if task_latencies_s else sum(llm_spans_s)

    latency: dict[str, Any] = {}
    request_latency = agentic_stat(latencies_ms, Units.MS)
    if request_latency:
        latency["request_latency"] = request_latency

    throughput: dict[str, Any] = {}
    if wall_s > 0:
        throughput["request_rate"] = {
            "units": Units.QUERY_PER_S,
            "mean": llm_calls / wall_s,
        }
        throughput["total_token_rate"] = {
            "units": Units.TOKEN_PER_S,
            "mean": (input_tokens + output_tokens) / wall_s,
        }

    observability: dict[str, Any] = {
        "eval_containers_tasks": len(tasks),
        "eval_containers_tasks_scored": scored,
        "eval_containers_passed_count": passed_count,
        "eval_containers_llm_calls": llm_calls,
        "eval_containers_call_errors": sum(t.call_errors for t in tasks),
        "eval_containers_retries": sum(t.retries for t in tasks),
        "eval_containers_input_tokens": input_tokens,
        "eval_containers_output_tokens": output_tokens,
        "eval_containers_total_tokens": input_tokens + output_tokens,
        "eval_containers_tasks_exit_timeout": sum(
            1 for t in tasks if t.exit_code == 124
        ),
        "eval_containers_tasks_exit_error": sum(
            1 for t in tasks if t.exit_code not in (0, 124, None)
        ),
        "eval_containers_ttft_valid_calls": len(ttft_s),
        "eval_containers_ttft_discarded_calls": sum(t.ttft_discarded for t in tasks),
    }
    if scored:
        observability["eval_containers_pass_rate"] = passed_count / scored
        observability["eval_containers_reward_mean"] = sum(rewards) / scored
        observability["eval_containers_reward_sum"] = sum(rewards)
    if llm_calls:
        observability["eval_containers_llm_calls_per_task_mean"] = llm_calls / len(
            tasks
        )

    # Per-language pass rates, so a task-mix shift is not mistaken for a
    # regression. Only meaningful for the language-ordered polyglot dataset.
    by_language: dict[str, list[_TaskMetrics]] = {}
    for task in tasks:
        if task.benchmark == "aider-polyglot":
            language = _polyglot_language(task.task_id)
            if language:
                by_language.setdefault(language, []).append(task)
    for language, group in sorted(by_language.items()):
        graded = [t for t in group if t.reward is not None]
        if not graded:
            continue
        hits = sum(1 for t in graded if t.passed)
        observability[f"eval_containers_pass_rate_{language}"] = hits / len(graded)
        observability[f"eval_containers_tasks_{language}"] = len(graded)

    # Task-level durations go in observability, not aggregate.latency:
    # AggregateLatency forbids extra fields and defines only per-REQUEST slots
    # (request_latency, TTFT, TPOT, ITL). A whole-task duration is not a request
    # latency, so it has no slot there -- observability is the schema's
    # extra-permitted area.
    task_latency_stat = agentic_stat(task_latencies_s, Units.S)
    if task_latency_stat:
        observability["eval_containers_task_latency_seconds"] = task_latency_stat
    llm_span_stat = agentic_stat(llm_spans_s, Units.S)
    if llm_span_stat:
        observability["eval_containers_task_llm_span_seconds"] = llm_span_stat
    ttft_stat = agentic_stat(ttft_s, Units.S)
    if ttft_stat:
        # Deliberately NOT aggregate.latency.time_to_first_token until the
        # emitter's unit is confirmed; see _record_ttft.
        observability["eval_containers_ttft_seconds"] = ttft_stat

    benchmarks = sorted({t.benchmark for t in tasks if t.benchmark})
    models = sorted({t.model for t in tasks if t.model})

    return {
        "version": "0.2",
        # run.uid is required by the schema. Reuse the experiment id the pods
        # already share (results dirs are named <experiment>_<index>) so this
        # report is traceable to the run that produced it, rather than minting an
        # unrelated identifier.
        "run": {"uid": _run_uid(tasks)},
        "scenario": {
            "load": {
                "metadata": {"schema_version": "0.0.1"},
                "standardized": {
                    "tool": "eval-containers",
                    "tool_version": "",
                    "source": "sampled",
                    "parallelism": len(tasks),
                    "input_seq_len": {"distribution": "other", "value": 0},
                },
                "native": {
                    "args": {
                        "harness": "eval-containers",
                        "benchmark": benchmarks[0]
                        if len(benchmarks) == 1
                        else ",".join(benchmarks),
                        "model": models[0] if len(models) == 1 else ",".join(models),
                        "workload_type": "agentic-multi-turn",
                        "aggregation": "run-level",
                        "tasks_completed": len(tasks),
                    }
                },
            }
        },
        "results": {
            "request_performance": {
                "aggregate": {"latency": latency, "throughput": throughput}
            },
            "observability": observability,
        },
    }


def _write_csv(tasks: list[_TaskMetrics], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for task in sorted(
            tasks, key=lambda t: (t.task_id if t.task_id is not None else -1, t.name)
        ):
            call_stat = agentic_stat(task.call_latencies_ms, Units.MS) or {}
            writer.writerow(
                {
                    "task_id": task.task_id if task.task_id is not None else "",
                    "benchmark": task.benchmark,
                    "language": _polyglot_language(task.task_id)
                    if task.benchmark == "aider-polyglot"
                    else "",
                    "passed": task.passed if task.passed is not None else "",
                    "reward": task.reward if task.reward is not None else "",
                    "exit_code": task.exit_code if task.exit_code is not None else "",
                    "harness_rc": task.harness_rc
                    if task.harness_rc is not None
                    else "",
                    "llm_calls": task.llm_calls,
                    "call_errors": task.call_errors,
                    "retries": task.retries,
                    "call_latency_mean_ms": call_stat.get("mean", ""),
                    "call_latency_p50_ms": call_stat.get("p50", ""),
                    "call_latency_max_ms": call_stat.get("max", ""),
                    "task_latency_s": task.task_latency_s
                    if task.task_latency_s is not None
                    else "",
                    "task_llm_span_s": task.task_llm_span_s
                    if task.task_llm_span_s is not None
                    else "",
                    "time_to_first_call_s": _time_to_first_call_s(task),
                    "input_tokens": task.input_tokens,
                    "output_tokens": task.output_tokens,
                    "total_tokens": task.input_tokens + task.output_tokens,
                }
            )


def _time_to_first_call_s(task: _TaskMetrics) -> float | str:
    """Seconds from the task's own start to its first LLM call.

    Requires the harness-recorded wall-clock: without a task start there is no
    anchor, and anchoring to the first span would define the answer as 0. Blank
    rather than fabricated when either input is missing.
    """
    if task.first_call_start_ns is None or task.harness_start_ns is None:
        return ""
    delta_s = (task.first_call_start_ns - task.harness_start_ns) / 1e9
    # A negative delta means the two clocks disagree (harness_start is written by
    # the pod's shell, span timestamps by the gateway), so the figure would be
    # meaningless rather than merely imprecise. Blank it instead.
    if delta_s < 0:
        return ""
    return delta_s


def generate_agentic_summary(
    results_dir: Path | str,
    output_dir: Path | str | None = None,
    context: Any = None,
) -> int:
    """Aggregate every eval-containers task under ``results_dir``.

    Returns the number of tasks aggregated (0 if there was nothing to do), so a
    caller can log it the way the cross-treatment step does.
    """
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        return 0

    task_dirs = _task_dirs(results_dir)
    if not task_dirs:
        _log(context, "No eval-containers task directories found for aggregation")
        return 0

    tasks = [_TaskMetrics(d) for d in task_dirs]

    output_dir = Path(output_dir) if output_dir else results_dir / "agentic-summary"
    output_dir.mkdir(parents=True, exist_ok=True)

    report = _build_report(tasks)
    report_path = output_dir / "agentic_run_report.yaml"
    # Round-trip through the schema rather than dumping the dict: it validates
    # the output (Statistics forbids extra keys, and units are checked per field)
    # and serializes the Units enum, which yaml.safe_dump cannot represent.
    load_benchmark_report(report).export_yaml(str(report_path))

    csv_path = output_dir / "agentic_tasks.csv"
    _write_csv(tasks, csv_path)

    observability = report["results"]["observability"]
    passed = observability.get("eval_containers_passed_count", 0)
    scored = observability.get("eval_containers_tasks_scored", 0)
    _log(
        context,
        f"Agentic summary: {len(tasks)} tasks, {passed}/{scored} passed, "
        f"{observability.get('eval_containers_llm_calls', 0)} LLM calls, "
        f"{observability.get('eval_containers_total_tokens', 0)} tokens -> {report_path}",
    )
    return len(tasks)
