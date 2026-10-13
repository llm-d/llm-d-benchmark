"""Tests for the eval-containers run summary pod / container and its helpers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps import step_07_deploy_harness as step07
from llmdbenchmark.run.steps.step_07_deploy_harness import (
    ROLE_LABEL,
    RUN_SUMMARY_ROLE,
    DeployHarnessStep,
    _TreatmentSpec,
)
from llmdbenchmark.run.steps.step_12_analyze_results import AnalyzeResultsStep
from llmdbenchmark.utilities.kube_helpers import (
    HARNESS_DONE_SENTINEL,
    RUN_SUMMARY_SCRIPT,
)

REPO = Path(__file__).resolve().parents[1]
RUN_SUMMARY_CONFIG = {"waitTimeout": 30, "resources": {"cpu": 1, "memory": "1Gi"}}


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def log_info(self, message: str, *_: Any, **__: Any) -> None:
        self.infos.append(message)

    def log_warning(self, message: str, *_: Any, **__: Any) -> None:
        self.warnings.append(message)

    def log_error(self, message: str, *_: Any, **__: Any) -> None:
        self.errors.append(message)

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("harness", "no_pvc", "debug", "expected"),
    [
        ("eval-containers", False, False, True),
        ("eval-containers", True, False, False),
        ("eval-containers", False, True, False),
        ("inference-perf", False, False, False),
    ],
)
def test_only_eval_containers_with_a_shared_volume_gets_a_summary(
    tmp_path: Path, harness: str, no_pvc: bool, debug: bool, expected: bool
) -> None:
    context = ExecutionContext(
        plan_dir=tmp_path, workspace=tmp_path, no_pvc=no_pvc, harness_debug=debug
    )
    assert DeployHarnessStep._wants_run_summary(harness, context) is expected


@pytest.mark.parametrize("exit_code", [0, 3])
def test_done_marker_keeps_the_task_exit_code(tmp_path: Path, exit_code: int) -> None:
    # The dir does not exist yet: a task can fail before making it.
    results = tmp_path / "exp_1"
    command = DeployHarnessStep._done_marker_command(f"exit {exit_code}", str(results))

    proc = subprocess.run(["sh", "-c", command], check=False)

    assert proc.returncode == exit_code
    marker = results / HARNESS_DONE_SENTINEL
    assert marker.read_text(encoding="utf-8").strip() == str(exit_code)


def test_run_summary_command_never_fails_the_pod() -> None:
    command = DeployHarnessStep._run_summary_command("/requests", "exp-1", 4, 3600)
    assert command == (
        f"python3 /usr/local/bin/{RUN_SUMMARY_SCRIPT.name} --expect 4 "
        f"--deadline 3600 /requests exp-1 || true"
    )


def test_defaults_carry_the_run_summary_settings() -> None:
    defaults = yaml.safe_load(
        (REPO / "config/templates/values/defaults.yaml").read_text(encoding="utf-8")
    )
    run_summary = defaults["harness"]["runSummary"]
    assert run_summary["waitTimeout"] > 0
    assert {"cpu", "memory"} <= set(run_summary["resources"])


def _render_pod_head(values: dict) -> dict:
    """Render the pod template up to its env block (labels and resources)."""
    template = (REPO / "config/templates/jinja/20_harness_pod.yaml.j2").read_text(
        encoding="utf-8"
    )
    head = template[template.index("apiVersion:") : template.index("    env:")]
    harness = {
        "podLabel": "llmdbench-harness-launcher",
        "resources": {"cpu": 16, "memory": "32Gi", "memoryLimit": "128Gi"},
    }
    return yaml.safe_load(
        DeployHarnessStep._render_template(
            head,
            {
                "pod_name": "p",
                "harness": harness,
                "namespace": {"name": "ns"},
                "images": {
                    "benchmark": {"repository": "r", "tag": "t", "pullPolicy": "Always"}
                },
                "harness_command": "true",
                "treatment_label_value": "t1",
                **values,
            },
        )
    )


def test_summary_pod_is_small_and_labeled() -> None:
    task = _render_pod_head({})
    summary = _render_pod_head(
        {"pod_role": RUN_SUMMARY_ROLE, "pod_resources": RUN_SUMMARY_CONFIG["resources"]}
    )

    assert ROLE_LABEL not in task["metadata"]["labels"]
    assert task["spec"]["containers"][0]["resources"]["requests"]["cpu"] == "16"

    labels = summary["metadata"]["labels"]
    # Cleanup selects on app and function, so the summary pod keeps both.
    assert labels["app"] == "llmdbench-harness-launcher"
    assert labels["function"] == "load_generator"
    assert labels["llmdbench.ai/treatment"] == "t1"
    assert labels[ROLE_LABEL] == RUN_SUMMARY_ROLE
    resources = summary["spec"]["containers"][0]["resources"]
    assert resources["requests"] == {"cpu": "1", "memory": "1Gi"}
    assert resources["limits"] == {"cpu": "1", "memory": "1Gi"}


# ---------------------------------------------------------------------------
# One treatment against a fake cluster
# ---------------------------------------------------------------------------


class _Applied:
    def __init__(self, success: bool = True) -> None:
        self.success = success
        self.stderr = "" if success else "exceeded quota"


class _KubeCmd:
    def __init__(self, fail_summary_apply: bool = False) -> None:
        self.applied: list[str] = []
        self.fail_summary_apply = fail_summary_apply

    def kube(self, *args: str, **_: Any) -> _Applied:
        path = Path(args[2])
        self.applied.append(path.read_text(encoding="utf-8"))
        is_summary = f"-{RUN_SUMMARY_ROLE}-" in path.name
        return _Applied(not (is_summary and self.fail_summary_apply))


def _spec(cmd: _KubeCmd, harness_name: str = "eval-containers") -> _TreatmentSpec:
    return _TreatmentSpec(
        treatment=None,
        index=1,
        total=1,
        group=None,
        cmd=cmd,
        plan_config={"harness": {"runSummary": RUN_SUMMARY_CONFIG}},
        harness_name=harness_name,
        harness_ns="ns",
        deploy_namespace="ns",
        endpoint_url="http://endpoint",
        model_label="model",
        model_name="model",
        stack_type="llm-d",
        profile_name="profile.yaml",
        profile_mounts=[],
        results_dir_prefix="/requests",
        harness_executable="llm-d-benchmark.sh",
        template_content=(
            "name: {{ pod_name }}\n"
            "role: {{ pod_role | default('') }}\n"
            "cpu: {{ (pod_resources | default({})).cpu | default('') }}\n"
            "command: {{ harness_command }}\n"
        ),
        pod_label="llmdbench-harness-launcher",
        parallelism=2,
        timeout=600,
    )


@pytest.fixture
def cluster(monkeypatch, tmp_path: Path):
    """Run one treatment with the cluster calls replaced by recorders."""
    waits: list[tuple[str, int]] = []
    deleted: list[str] = []
    wait_errors: dict[str, list[str]] = {}

    def _wait(_cmd, selector, _ns, timeout, _context):
        waits.append((selector, timeout))
        return wait_errors.get("summary" if "role=" in selector else "tasks", [])

    monkeypatch.setattr(step07, "wait_for_pods_by_selector", _wait)
    monkeypatch.setattr(
        step07, "delete_pods_by_names", lambda _c, names, *_a: deleted.extend(names)
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        staticmethod(lambda *_a, **_k: (None, {}, [])),
    )

    def _run(cmd: _KubeCmd, harness_name: str = "eval-containers"):
        logger = _Logger()
        context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=logger)
        context.run_dir().mkdir(parents=True, exist_ok=True)
        result = DeployHarnessStep()._run_treatment(_spec(cmd, harness_name), context)
        return result, logger

    return _run, waits, deleted, wait_errors


def _docs(cmd: _KubeCmd) -> list[dict]:
    return [yaml.safe_load(text) for text in cmd.applied]


def test_treatment_adds_one_summary_pod_after_its_tasks(cluster) -> None:
    run, waits, deleted, _ = cluster
    cmd = _KubeCmd()

    (succeeded, errors, deployed), _ = run(cmd)

    assert succeeded, errors
    assert deployed == 2
    docs = _docs(cmd)
    assert [d["role"] for d in docs] == [None, None, RUN_SUMMARY_ROLE]
    assert docs[2]["cpu"] == 1
    assert "--expect 2 --deadline 600 /requests" in docs[2]["command"]
    for task in docs[:2]:
        assert HARNESS_DONE_SENTINEL in task["command"]
        assert "exit $rc" in task["command"]

    # The tasks are waited on without the summary, then the summary alone.
    (task_selector, task_timeout), (summary_selector, summary_timeout) = waits
    assert task_selector.endswith(f"{ROLE_LABEL}!={RUN_SUMMARY_ROLE}")
    assert task_timeout == 600
    assert summary_selector.endswith(f"{ROLE_LABEL}={RUN_SUMMARY_ROLE}")
    assert summary_timeout == RUN_SUMMARY_CONFIG["waitTimeout"]
    # Cleanup removes the summary pod with the tasks.
    assert len(deleted) == 3
    assert any(f"-{RUN_SUMMARY_ROLE}-" in name for name in deleted)


def test_unfinished_summary_only_warns(cluster) -> None:
    run, _, deleted, wait_errors = cluster
    wait_errors["summary"] = ["Pods did not complete within 30s"]

    (succeeded, errors, _), logger = run(_KubeCmd())

    assert succeeded, errors
    assert any("Run summary not written" in w for w in logger.warnings)
    assert any(f"-{RUN_SUMMARY_ROLE}-" in name for name in deleted)


def test_summary_pod_that_cannot_be_created_only_warns(cluster) -> None:
    run, waits, deleted, _ = cluster

    (succeeded, errors, deployed), logger = run(_KubeCmd(fail_summary_apply=True))

    assert succeeded, errors
    assert deployed == 2
    assert any("run summary pod" in w for w in logger.warnings)
    assert len(waits) == 1
    assert not any(f"-{RUN_SUMMARY_ROLE}-" in name for name in deleted)


def test_failed_tasks_skip_the_summary_wait(cluster) -> None:
    run, waits, _, wait_errors = cluster
    wait_errors["tasks"] = ["Pods did not complete within 600s"]

    (succeeded, _, _), _ = run(_KubeCmd())

    assert not succeeded
    assert len(waits) == 1


def test_other_harnesses_get_no_summary_pod(cluster) -> None:
    run, waits, _, _ = cluster
    cmd = _KubeCmd()

    (succeeded, errors, _), _ = run(cmd, harness_name="inference-perf")

    assert succeeded, errors
    docs = _docs(cmd)
    assert len(docs) == 2
    assert all(d["role"] is None for d in docs)
    assert all(HARNESS_DONE_SENTINEL not in d["command"] for d in docs)
    assert len(waits) == 1


# ---------------------------------------------------------------------------
# nok8s: a summary container next to the task containers
# ---------------------------------------------------------------------------


def _nok8s(tmp_path: Path, harness: str, connection: str = ""):
    from test_nok8s_harness_status import (
        DeployHarnessLocalStep,
        _Command,
        _context,
        _plan_config,
        _Result,
    )

    replies = {" inspect ": _Result(stdout="exited 0\n")}
    if connection:
        replies["printenv HOME"] = _Result(stdout="/home/bench\n")
    cmd = _Command(replies)
    logger = _Logger()
    context, stack_path = _context(tmp_path, cmd, logger)
    context.harness_parallelism = 2
    config = _plan_config()
    config["harness"]["name"] = harness
    config["harness"]["runSummary"] = RUN_SUMMARY_CONFIG
    if connection:
        config["nok8s"]["connection"] = connection
    (stack_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    result = DeployHarnessLocalStep().execute(context, stack_path)
    return result, cmd, logger


def _runs(cmd) -> list[str]:
    return [c for c in cmd.commands if " run -d --name " in c]


def test_nok8s_starts_a_summary_container_after_the_tasks(tmp_path: Path) -> None:
    result, cmd, _ = _nok8s(tmp_path, "eval-containers")

    assert result.success, result.errors
    runs = _runs(cmd)
    assert len(runs) == 3
    summary = runs[2]
    assert re.search(r"--name eval-containers-\S+-summary ", summary)
    script = REPO / RUN_SUMMARY_SCRIPT
    assert f"-v {script}:/tmp/{RUN_SUMMARY_SCRIPT.name}:ro " in summary

    entry = next(Path(tmp_path).rglob(".harness-entry-*-summary.sh"))
    assert f"/tmp/{RUN_SUMMARY_SCRIPT.name} --expect 2" in entry.read_text()

    # Waited on after the task containers, with its own timeout.
    waits = [c for c in cmd.commands if " wait " in c]
    assert len(waits) == 2
    assert "-summary" not in waits[0]
    assert waits[1].startswith(f"timeout {RUN_SUMMARY_CONFIG['waitTimeout']} ")
    assert "-summary" in waits[1]


def test_nok8s_tasks_write_the_done_marker(tmp_path: Path) -> None:
    _nok8s(tmp_path, "eval-containers")

    tasks = [
        p
        for p in Path(tmp_path).rglob(".harness-entry-*.sh")
        if not p.name.endswith("-summary.sh")
    ]
    assert len(tasks) == 2
    for entry in tasks:
        assert HARNESS_DONE_SENTINEL in entry.read_text()


def test_nok8s_unfinished_summary_only_warns(tmp_path: Path) -> None:
    from test_nok8s_harness_status import (
        DeployHarnessLocalStep,
        _Command,
        _context,
        _plan_config,
        _Result,
    )

    # Tasks exit 0; the summary is still running when its wait ends.
    class _SummaryStuck(_Command):
        def execute(self, command: str, *a: Any, **k: Any) -> _Result:
            if " inspect " in command and "-summary" in command:
                self.commands.append(command)
                return _Result(stdout="running 0\n")
            return super().execute(command, *a, **k)

    cmd = _SummaryStuck({" inspect ": _Result(stdout="exited 0\n")})
    logger = _Logger()
    context, stack_path = _context(tmp_path, cmd, logger)
    config = _plan_config()
    config["harness"]["name"] = "eval-containers"
    config["harness"]["runSummary"] = RUN_SUMMARY_CONFIG
    (stack_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    result = DeployHarnessLocalStep().execute(context, stack_path)

    assert result.success, result.errors
    assert any("Run summary not written" in w for w in logger.warnings)


def test_nok8s_remote_pushes_the_summary_script(tmp_path: Path) -> None:
    result, cmd, _ = _nok8s(tmp_path, "eval-containers", "ssh://bench@10.0.0.7")

    assert result.success, result.errors
    remote_root = f"/home/bench/.llmdbench/nok8s-runs/stack/{tmp_path.name}"
    target = f"{remote_root}/{RUN_SUMMARY_SCRIPT.name}"
    push = next(i for i, c in enumerate(cmd.commands) if target in c and "scp " in c)
    summary = next(
        i for i, c in enumerate(cmd.commands) if "-summary " in c and " run -d " in c
    )
    assert push < summary
    assert f"-v {target}:/tmp/{RUN_SUMMARY_SCRIPT.name}:ro " in cmd.commands[summary]


def test_nok8s_other_harnesses_get_no_summary_container(tmp_path: Path) -> None:
    result, cmd, _ = _nok8s(tmp_path, "inference-perf")

    assert result.success, result.errors
    assert len(_runs(cmd)) == 2
    assert not any("-summary" in c for c in cmd.commands)


# ---------------------------------------------------------------------------
# Step 12 and the in-pod analyzers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "skipped"),
    [
        ({}, False),
        ({"harness_debug": True}, True),
        ({"harness_data_collect": "skip"}, True),
        ({"harness_data_collect": "results"}, False),
    ],
)
def test_step_12_always_runs_unless_nothing_is_local(
    tmp_path: Path, kwargs: dict, skipped: bool
) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, **kwargs)
    assert AnalyzeResultsStep().should_skip(context) is skipped


def test_step_12_warns_instead_of_failing(tmp_path: Path, monkeypatch) -> None:
    from llmdbenchmark.run.steps import step_12_analyze_results as step12

    logger = _Logger()
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=logger)
    (context.run_results_dir() / "exp_1").mkdir(parents=True)
    monkeypatch.setattr(step12, "run_analysis", lambda *_a: "conversion failed")

    result = AnalyzeResultsStep().execute(context)

    assert result.success
    assert any("conversion failed" in w for w in logger.warnings)


def test_every_metrics_collecting_harness_embeds_its_metrics() -> None:
    """A harness that scrapes metrics but never embeds them ships reports
    without a time series."""
    harnesses = REPO / "workload" / "harnesses"
    analyzers = REPO / "llmdbenchmark" / "analysis" / "scripts"
    collecting = sorted(
        path.name.removesuffix("-llm-d-benchmark.sh")
        for path in harnesses.glob("*-llm-d-benchmark.sh")
        if "collect_metrics.sh process" in path.read_text(encoding="utf-8")
    )
    assert "aiperf" in collecting
    for harness in collecting:
        analyzer = analyzers / f"{harness}-analyze_results.sh"
        assert "embed_metrics.py" in analyzer.read_text(encoding="utf-8"), harness
