"""Tests for the run-loop control knobs: ``read_run_controls`` parsing and the
step_07 ``_validate_failures`` / ``_delete_faulty_results`` retry helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.experiment.parser import RUN_CONTROL_DEFAULTS, read_run_controls

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_07_deploy_harness.py"
)
_spec = importlib.util.spec_from_file_location("step_07_run_controls", _STEP_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["step_07_run_controls"] = _mod
_spec.loader.exec_module(_mod)
DeployHarnessStep = _mod.DeployHarnessStep


# ---------------------------------------------------------------------------
# read_run_controls
# ---------------------------------------------------------------------------


def test_read_run_controls_none_returns_defaults():
    assert read_run_controls(None) == RUN_CONTROL_DEFAULTS


def test_read_run_controls_missing_file_returns_defaults(tmp_path: Path):
    assert read_run_controls(tmp_path / "nope.yaml") == RUN_CONTROL_DEFAULTS


def test_read_run_controls_reads_all_keys(tmp_path: Path):
    p = tmp_path / "exp.yaml"
    p.write_text(
        "treatment_max_attempts: 5\n"
        "treatment_stop_on_error: true\n"
        "validate_failures: true\n"
        "treatments:\n  - {name: a, foo: 1}\n",
        encoding="utf-8",
    )
    controls = read_run_controls(p)
    assert controls == {
        "treatment_max_attempts": 5,
        "treatment_stop_on_error": True,
        "validate_failures": True,
    }


def test_read_run_controls_absent_keys_fall_back(tmp_path: Path):
    p = tmp_path / "exp.yaml"
    p.write_text("treatments:\n  - {name: a, foo: 1}\n", encoding="utf-8")
    assert read_run_controls(p) == RUN_CONTROL_DEFAULTS


def test_read_run_controls_clamps_attempts_to_min_one(tmp_path: Path):
    p = tmp_path / "exp.yaml"
    p.write_text("treatment_max_attempts: 0\n", encoding="utf-8")
    assert read_run_controls(p)["treatment_max_attempts"] == 1


def test_read_run_controls_bad_attempts_falls_back(tmp_path: Path):
    p = tmp_path / "exp.yaml"
    p.write_text("treatment_max_attempts: not-a-number\n", encoding="utf-8")
    assert read_run_controls(p)["treatment_max_attempts"] == 1


def test_read_run_controls_non_mapping_returns_defaults(tmp_path: Path):
    p = tmp_path / "exp.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert read_run_controls(p) == RUN_CONTROL_DEFAULTS


# ---------------------------------------------------------------------------
# _validate_failures
# ---------------------------------------------------------------------------


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def log_warning(self, message, *_a, **_k) -> None:
        self.warnings.append(message)

    def __getattr__(self, _name):  # log_info/log_error/etc. are no-ops
        return lambda *a, **k: None


# The otel_traces workload is the one _validate_failures knows how to read.
_OTEL = "otel_traces.yaml"


def _ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(workspace=tmp_path, plan_dir=tmp_path, logger=_Logger())


def _write_summary(pod_dir: Path, failures_count):
    pod_dir.mkdir(parents=True, exist_ok=True)
    (pod_dir / "summary_lifecycle_metrics.json").write_text(
        json.dumps({"failures": {"count": failures_count}}), encoding="utf-8"
    )


def test_validate_failures_passes_when_zero(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    _write_summary(ctx.run_results_dir() / f"{eid}_1", 0)
    assert DeployHarnessStep()._validate_failures(ctx, eid, 1, _OTEL) == []


def test_validate_failures_flags_positive_count(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    _write_summary(ctx.run_results_dir() / f"{eid}_1", 3)
    errs = DeployHarnessStep()._validate_failures(ctx, eid, 1, _OTEL)
    assert len(errs) == 1 and "3 failed session" in errs[0]


def test_validate_failures_flags_missing_file(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    # No dir/file created.
    errs = DeployHarnessStep()._validate_failures(ctx, eid, 1, _OTEL)
    assert len(errs) == 1 and "missing" in errs[0]


def test_validate_failures_analysis_fallback(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    pod_dir = ctx.run_results_dir() / f"{eid}_1"
    _write_summary(pod_dir / "analysis", 0)  # only under analysis/
    assert DeployHarnessStep()._validate_failures(ctx, eid, 1, _OTEL) == []


def test_validate_failures_unparseable(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    pod_dir = ctx.run_results_dir() / f"{eid}_1"
    pod_dir.mkdir(parents=True, exist_ok=True)
    (pod_dir / "summary_lifecycle_metrics.json").write_text("{bad", encoding="utf-8")
    errs = DeployHarnessStep()._validate_failures(ctx, eid, 1, _OTEL)
    assert len(errs) == 1 and "cannot parse" in errs[0]


def test_validate_failures_checks_each_parallel_pod(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    _write_summary(ctx.run_results_dir() / f"{eid}_1", 0)
    _write_summary(ctx.run_results_dir() / f"{eid}_2", 5)
    errs = DeployHarnessStep()._validate_failures(ctx, eid, 2, _OTEL)
    assert len(errs) == 1 and "_2" in errs[0]


def test_validate_failures_accepts_dot_in_suffix(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    _write_summary(ctx.run_results_dir() / f"{eid}_1", 0)
    # profile name may carry the .yaml.in / path form.
    assert (
        DeployHarnessStep()._validate_failures(
            ctx, eid, 1, "workload/profiles/inference-perf/otel_traces.yaml.in"
        )
        == []
    )


def test_validate_failures_falls_back_for_other_workload(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "vllm-benchmark-conc8-1-abc"
    # A positive count would fail for otel, but this is a different workload:
    # the check must be skipped (no errors) and a warning logged.
    _write_summary(ctx.run_results_dir() / f"{eid}_1", 9)
    errs = DeployHarnessStep()._validate_failures(ctx, eid, 1, "random_concurrent.yaml")
    assert errs == []
    assert any("no result-failure check implemented" in w for w in ctx.logger.warnings)


def test_validate_failures_falls_back_for_unknown_profile(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    errs = DeployHarnessStep()._validate_failures(ctx, eid, 1, None)
    assert errs == []
    assert ctx.logger.warnings  # warned about the missing/unknown profile


# ---------------------------------------------------------------------------
# _delete_faulty_results
# ---------------------------------------------------------------------------


def test_delete_faulty_results_removes_pod_dirs(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    d1 = ctx.run_results_dir() / f"{eid}_1"
    d2 = ctx.run_results_dir() / f"{eid}_2"
    _write_summary(d1, 0)
    _write_summary(d2, 0)
    # An unrelated treatment dir must survive.
    keep = ctx.run_results_dir() / "inference-perf-conc16-2-xyz_1"
    _write_summary(keep, 0)

    DeployHarnessStep._delete_faulty_results(ctx, eid, 2)

    assert not d1.exists()
    assert not d2.exists()
    assert keep.exists()


def test_delete_faulty_results_sweeps_discovery_named_dirs(tmp_path: Path):
    ctx = _ctx(tmp_path)
    eid = "inference-perf-conc8-1-abc"
    # A discovery-named dir that contains the experiment_id but not the _<i> form.
    disc = ctx.run_results_dir() / f"{eid}_run_extra"
    disc.mkdir(parents=True, exist_ok=True)
    DeployHarnessStep._delete_faulty_results(ctx, eid, 1)
    assert not disc.exists()
