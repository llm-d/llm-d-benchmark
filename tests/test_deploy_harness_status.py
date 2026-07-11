"""Tests for harness deployment status reporting."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.context import ExecutionContext

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_07_deploy_harness.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_07_deploy_harness_status", _STEP_PATH
)
deploy_harness = importlib.util.module_from_spec(_spec)
sys.modules["step_07_deploy_harness_status"] = deploy_harness
_spec.loader.exec_module(deploy_harness)
DeployHarnessStep = deploy_harness.DeployHarnessStep


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.errors: list[str] = []

    def log_info(self, message: str, *_: Any, **__: Any) -> None:
        self.infos.append(message)

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, message: str, *_: Any, **__: Any) -> None:
        self.errors.append(message)

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


class _Result:
    success = True
    stdout = ""
    stderr = ""
    dry_run = False


class _Command:
    def kube(self, *args: str, **_: Any) -> _Result:
        assert args[:2] == ("apply", "-f")
        return _Result()


def _plan_config() -> dict[str, Any]:
    return {
        "namespace": {"name": "bench"},
        "model": {"name": "test-model"},
        "images": {
            "benchmark": {
                "repository": "example.com/bench",
                "tag": "latest",
                "pullPolicy": "IfNotPresent",
            }
        },
        "harness": {
            "name": "inference-perf",
            "namespace": "bench",
            "podLabel": "llmdbench-harness-launcher",
            "resources": {"cpu": "1", "memory": "1Gi"},
            "inferencePerf": {"rayonNumThreads": "1"},
            "resultsDirPrefix": "/requests",
            "stackName": "model",
        },
        "experiment": {"workspaceDir": "/workspace", "resultsDir": "/requests"},
        "vllmCommon": {"inferencePort": 8000},
        "standalone": {
            "enabled": False,
            "launcher": {"enabled": False},
            "vllm": {"loadFormat": "auto"},
        },
        "fma": {"enabled": False},
        "storage": {"workloadPvc": {"name": "workload-pvc"}},
        "huggingface": {"enabled": False},
    }


def test_treatment_with_wait_errors_is_reported_failed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    stack_path = tmp_path / "plan" / "stack"
    stack_path.mkdir(parents=True)
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(_plan_config()),
        encoding="utf-8",
    )

    logger = _Logger()
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        base_dir=Path(__file__).resolve().parents[1],
        namespace="bench",
        harness_namespace="bench",
        logger=logger,
        cmd=_Command(),
    )
    context.deployed_endpoints["stack"] = "http://endpoint"

    monkeypatch.setattr(
        deploy_harness,
        "wait_for_pods_by_label",
        lambda *_args, **_kwargs: ["harness pod failed"],
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        deploy_harness,
        "delete_pods_by_names",
        lambda *_args, **_kwargs: None,
    )

    result = DeployHarnessStep().execute(context, stack_path)

    assert not result.success
    assert "harness pod failed" in result.errors
    assert any("Treatment 'default' failed" in error for error in logger.errors)
    assert not any("Treatment 'default' complete" in info for info in logger.infos)


def _base_context(tmp_path: Path, logger: _Logger) -> ExecutionContext:
    stack_path = tmp_path / "plan" / "stack"
    stack_path.mkdir(parents=True)
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(_plan_config()),
        encoding="utf-8",
    )
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        base_dir=Path(__file__).resolve().parents[1],
        namespace="bench",
        harness_namespace="bench",
        logger=logger,
        cmd=_Command(),
    )
    context.deployed_endpoints["stack"] = "http://endpoint"
    return context, stack_path


def _patch_run_helpers(monkeypatch: Any) -> None:
    """Stub out the wait/collect/delete helpers so the loop completes."""
    monkeypatch.setattr(
        deploy_harness,
        "wait_for_pods_by_label",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        deploy_harness,
        "delete_pods_by_names",
        lambda *_a, **_k: None,
    )


def test_reset_caches_called_once_per_treatment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.reset_caches = True
    context.experiment_treatments = [
        {"name": "t0", "overrides": {}},
        {"name": "t1", "overrides": {}},
    ]

    calls: list[tuple] = []
    monkeypatch.setattr(
        deploy_harness,
        "reset_caches_pods",
        lambda cmd, ns, label, port, **kw: calls.append((ns, label, port)) or [],
    )
    _patch_run_helpers(monkeypatch)

    result = DeployHarnessStep().execute(context, stack_path)

    assert result.success
    # One reset per treatment, targeting the serving namespace, the model
    # label, and vllmCommon.inferencePort from the fixture.
    assert len(calls) == 2
    assert all(ns == "bench" for ns, _label, _port in calls)
    assert all(port == 8000 for _ns, _label, port in calls)


def test_reset_caches_not_called_when_disabled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    # reset_caches defaults to False.
    context.experiment_treatments = [{"name": "t0", "overrides": {}}]

    calls: list[tuple] = []
    monkeypatch.setattr(
        deploy_harness,
        "reset_caches_pods",
        lambda *a, **k: calls.append(a) or [],
    )
    _patch_run_helpers(monkeypatch)

    result = DeployHarnessStep().execute(context, stack_path)

    assert result.success
    assert calls == []


def test_reset_caches_skipped_in_dry_run(tmp_path: Path, monkeypatch: Any) -> None:
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.reset_caches = True
    context.dry_run = True
    context.experiment_treatments = [{"name": "t0", "overrides": {}}]

    calls: list[tuple] = []
    monkeypatch.setattr(
        deploy_harness,
        "reset_caches_pods",
        lambda *a, **k: calls.append(a) or [],
    )

    DeployHarnessStep().execute(context, stack_path)

    assert calls == []


def test_treatment_retries_then_succeeds(tmp_path: Path, monkeypatch: Any) -> None:
    """A treatment that fails once then passes succeeds within max_attempts."""
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.treatment_max_attempts = 3
    context.experiment_treatments = [{"name": "t0", "overrides": {}}]

    # Fail the wait on the first attempt, pass on the second.
    attempts = {"n": 0}

    def _wait(*_a, **_k):
        attempts["n"] += 1
        return ["wait failed"] if attempts["n"] == 1 else []

    monkeypatch.setattr(deploy_harness, "wait_for_pods_by_label", _wait)
    monkeypatch.setattr(
        DeployHarnessStep, "_collect_treatment_results_discovery", lambda *_a, **_k: []
    )
    monkeypatch.setattr(deploy_harness, "delete_pods_by_names", lambda *_a, **_k: None)

    deleted: list[str] = []
    monkeypatch.setattr(
        DeployHarnessStep,
        "_delete_faulty_results",
        staticmethod(lambda _ctx, eid, _par: deleted.append(eid)),
    )

    result = DeployHarnessStep().execute(context, stack_path)

    assert result.success
    assert attempts["n"] == 2  # retried once
    assert len(deleted) == 1  # faulty results cleaned before the retry
    assert len(context.experiment_ids) == 1  # only the successful attempt recorded


def test_treatment_exhausts_attempts_records_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.treatment_max_attempts = 2
    context.experiment_treatments = [{"name": "t0", "overrides": {}}]

    monkeypatch.setattr(
        deploy_harness, "wait_for_pods_by_label", lambda *_a, **_k: ["wait failed"]
    )
    monkeypatch.setattr(
        DeployHarnessStep, "_collect_treatment_results_discovery", lambda *_a, **_k: []
    )
    monkeypatch.setattr(deploy_harness, "delete_pods_by_names", lambda *_a, **_k: None)
    monkeypatch.setattr(
        DeployHarnessStep, "_delete_faulty_results", staticmethod(lambda *_a: None)
    )

    result = DeployHarnessStep().execute(context, stack_path)

    assert not result.success
    assert "wait failed" in result.errors
    # A failed treatment records no experiment_id (kept out of the upload step).
    assert context.experiment_ids == []


def test_stop_on_error_aborts_remaining_treatments(
    tmp_path: Path, monkeypatch: Any
) -> None:
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.treatment_stop_on_error = True
    context.experiment_treatments = [
        {"name": "t0", "overrides": {}},
        {"name": "t1", "overrides": {}},
    ]

    seen: list[str] = []

    def _collect(_cmd, experiment_id, *_a, **_k):
        seen.append(experiment_id)
        return []

    # Fail the first treatment's wait; it has no retries (default 1), so
    # stop_on_error should abort before the second treatment runs.
    monkeypatch.setattr(
        deploy_harness, "wait_for_pods_by_label", lambda *_a, **_k: ["wait failed"]
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        staticmethod(_collect),
    )
    monkeypatch.setattr(deploy_harness, "delete_pods_by_names", lambda *_a, **_k: None)

    result = DeployHarnessStep().execute(context, stack_path)

    assert not result.success
    # Only the first treatment (t0) ran; t1 was never collected.
    assert all("-t0-" in eid for eid in seen)
    assert not any("-t1-" in eid for eid in seen)


def test_validate_failures_fails_clean_run(tmp_path: Path, monkeypatch: Any) -> None:
    """A pod that exits clean but reports failures.count>0 fails the treatment."""
    import json as _json

    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.validate_failures = True
    # The failures.count check is specific to the otel_traces workload.
    context.harness_profile = "otel_traces.yaml"
    context.experiment_treatments = [{"name": "t0", "overrides": {}}]

    # Collect writes a summary with a positive failure count into the pod dir.
    def _collect(_cmd, experiment_id, *_a, **_k):
        d = context.run_results_dir() / f"{experiment_id}_1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary_lifecycle_metrics.json").write_text(
            _json.dumps({"failures": {"count": 2}}), encoding="utf-8"
        )
        return []

    monkeypatch.setattr(deploy_harness, "wait_for_pods_by_label", lambda *_a, **_k: [])
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        staticmethod(_collect),
    )
    monkeypatch.setattr(deploy_harness, "delete_pods_by_names", lambda *_a, **_k: None)

    result = DeployHarnessStep().execute(context, stack_path)

    assert not result.success
    assert any("failed session" in e for e in result.errors)


def test_validate_failures_falls_back_for_non_otel_workload(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """validate_failures on a non-otel workload warns and passes on pod state."""
    import json as _json

    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.validate_failures = True
    context.harness_profile = "random_concurrent.yaml"  # not otel_traces
    context.experiment_treatments = [{"name": "t0", "overrides": {}}]

    def _collect(_cmd, experiment_id, *_a, **_k):
        d = context.run_results_dir() / f"{experiment_id}_1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary_lifecycle_metrics.json").write_text(
            _json.dumps({"failures": {"count": 7}}), encoding="utf-8"
        )
        return []

    monkeypatch.setattr(deploy_harness, "wait_for_pods_by_label", lambda *_a, **_k: [])
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        staticmethod(_collect),
    )
    monkeypatch.setattr(deploy_harness, "delete_pods_by_names", lambda *_a, **_k: None)

    result = DeployHarnessStep().execute(context, stack_path)

    # The positive failure count is ignored for a non-otel workload: the pods
    # exited clean, so the treatment succeeds.
    assert result.success


def test_debug_harness_uses_generic_name_and_mounts_all_profiles(
    tmp_path: Path,
) -> None:
    logger = _Logger()
    context, stack_path = _base_context(tmp_path, logger)
    context.harness_debug = True
    profiles_root = context.workload_profiles_dir()
    (profiles_root / "guidellm").mkdir(parents=True)
    (profiles_root / "inference-perf").mkdir(parents=True)
    (profiles_root / "guidellm" / "chat.yaml").write_text(
        "profile: constant\n", encoding="utf-8"
    )
    (profiles_root / "inference-perf" / "sanity.yaml").write_text(
        "target: endpoint\n", encoding="utf-8"
    )

    result = DeployHarnessStep().execute(context, stack_path)

    assert result.success
    pod_files = list(context.run_dir().glob("llmdbench-harness-debug-*.yaml"))
    assert len(pod_files) == 1
    pod = yaml.safe_load(pod_files[0].read_text(encoding="utf-8"))
    assert pod["metadata"]["name"].startswith("llmdbench-harness-debug-")
    container = pod["spec"]["containers"][0]
    mounts = {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]}
    assert mounts["guidellm-profiles"] == "/workspace/profiles/guidellm"
    assert mounts["inference-perf-profiles"] == "/workspace/profiles/inference-perf"
