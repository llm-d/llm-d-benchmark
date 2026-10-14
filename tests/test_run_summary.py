"""Tests for the eval-containers report sidecar and run summary."""

from __future__ import annotations

import copy
import functools
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps import step_07_deploy_harness as step07
from llmdbenchmark.run.steps.step_07_deploy_harness import (
    REPORT_CONTAINER,
    ROLE_LABEL,
    RUN_SUMMARY_ROLE,
    DeployHarnessStep,
    _TreatmentSpec,
)
from llmdbenchmark.run.steps.step_12_analyze_results import AnalyzeResultsStep
from llmdbenchmark.utilities import kube_helpers
from llmdbenchmark.utilities.kube_helpers import (
    HARNESS_DONE_SENTINEL,
    RUN_SUMMARY_SCRIPT,
    TASK_EXIT_MARKER,
)

REPO = Path(__file__).resolve().parents[1]
EVAL_IMAGE = {"repository": "example.com/eval", "tag": "v1"}
HELPER_RESOURCES = {"cpu": "100m", "cpuLimit": 1, "memory": "256Mi"}
RUN_SUMMARY_CONFIG = {"waitTimeout": 30}
EVAL_HARNESS = {
    "runSummary": RUN_SUMMARY_CONFIG,
    "helperResources": HELPER_RESOURCES,
    "evalImage": EVAL_IMAGE,
}


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


def _sh(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", "-c", command], check=False, timeout=10)


# ---------------------------------------------------------------------------
# Commands
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


def test_pod_commands_survive_kubelet_expansion() -> None:
    commands = [
        DeployHarnessStep._task_exit_command("true", "/r/e_1"),
        DeployHarnessStep._task_report_command("/r/e_1", "/r", 60),
        DeployHarnessStep._task_report_command("/r/e_1", "/r", 60, no_pvc=True),
    ]
    assert not any("$$" in command for command in commands)


def _fake_analyzer(tmp_path: Path) -> Path:
    analyzer = tmp_path / "analyzer.sh"
    analyzer.write_text(
        '#!/bin/sh\ntouch "$LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR/analyzed"\n',
        encoding="utf-8",
    )
    analyzer.chmod(0o755)
    return analyzer


def _report(tmp_path: Path, timeout: int = 60, **kwargs: Any) -> str:
    return DeployHarnessStep._task_report_command(
        str(tmp_path / "exp_1"),
        str(tmp_path),
        timeout,
        analyzer=str(_fake_analyzer(tmp_path)),
        poll=1,
        **kwargs,
    )


def test_report_runs_after_the_task_and_copies_its_exit_code(tmp_path: Path) -> None:
    results = tmp_path / "exp_1"
    results.mkdir()
    (results / TASK_EXIT_MARKER).write_text("3\n", encoding="utf-8")

    assert _sh(_report(tmp_path)).returncode == 0

    assert (results / "analyzed").exists()
    assert (results / HARNESS_DONE_SENTINEL).read_text().strip() == "3"


def test_report_marks_the_pod_done_without_a_pvc(tmp_path: Path) -> None:
    results = tmp_path / "exp_1"
    results.mkdir()
    (results / TASK_EXIT_MARKER).write_text("0\n", encoding="utf-8")
    command = _report(tmp_path, no_pvc=True)
    assert command.endswith("; sleep infinity")
    assert "then sleep infinity; fi" in command

    _sh(command.removesuffix("; sleep infinity"))

    assert (tmp_path / HARNESS_DONE_SENTINEL).read_text().strip() == "0"


def test_run_summary_outlasts_the_report_sidecars() -> None:
    assert DeployHarnessStep._run_summary_deadline(600, RUN_SUMMARY_CONFIG) == 630
    assert DeployHarnessStep._run_summary_deadline(0, RUN_SUMMARY_CONFIG) == 0
    command = DeployHarnessStep._run_summary_command("/requests", "exp-1", 4, 630)
    assert command == (
        f"python3 /workspace/harnesses/{RUN_SUMMARY_SCRIPT.name} --expect 4 "
        f"--deadline 630 /requests exp-1 || true"
    )


# ---------------------------------------------------------------------------
# Config and pod template
# ---------------------------------------------------------------------------


@functools.cache
def _defaults() -> dict:
    return yaml.safe_load(
        (REPO / "config/templates/values/defaults.yaml").read_text(encoding="utf-8")
    )


def test_defaults_keep_the_benchmark_image_for_helpers() -> None:
    harness = _defaults()["harness"]
    assert harness["evalImage"] is None
    assert harness["runSummary"]["waitTimeout"] > 0
    assert {"cpu", "cpuLimit", "memory", "memoryLimit"} <= set(
        harness["helperResources"]
    )


@pytest.mark.parametrize(
    "scenario",
    sorted((REPO / "config/scenarios/experimental").glob("eval-containers-*")),
)
def test_eval_scenarios_set_the_eval_image_only_for_tasks(scenario: Path) -> None:
    stack = yaml.safe_load(scenario.read_text(encoding="utf-8"))["scenario"][0]
    assert "benchmark" not in stack.get("images", {})
    assert stack["harness"]["evalImage"]["repository"]


@functools.cache
def _template() -> str:
    jinja = REPO / "config/templates/jinja"
    return (jinja / "_macros.j2").read_text() + (
        jinja / "20_harness_pod.yaml.j2"
    ).read_text()


def _render_pod(harness: dict | None = None, **values: Any) -> dict:
    plan = copy.deepcopy(_defaults())
    plan["harness"].update({"namespace": "ns", **(harness or {})})
    plan["images"]["benchmark"] = {
        "repository": "r",
        "tag": "t",
        "pullPolicy": "Always",
    }
    plan.update(
        {
            "pod_name": "p",
            "harness_command": "true",
            "experiment_id": "exp",
            "results_dir": "/requests/exp_1",
            "treatment_label_value": "t1",
            **values,
        }
    )
    return yaml.safe_load(DeployHarnessStep._render_template(_template(), plan))


EVAL_POD = {"name": "eval-containers", "evalImage": EVAL_IMAGE}


def test_other_harness_pods_are_unchanged() -> None:
    pod = _render_pod()
    assert [c["name"] for c in pod["spec"]["containers"]] == ["harness"]
    assert pod["spec"]["containers"][0]["image"] == "r:t"
    assert "shareProcessNamespace" not in pod["spec"]
    assert "annotations" not in pod["metadata"]


def test_eval_task_pod_has_a_report_sidecar() -> None:
    pod = _render_pod(EVAL_POD, report_command="run-report")

    harness, report = pod["spec"]["containers"]
    assert (harness["name"], report["name"]) == ("harness", REPORT_CONTAINER)
    assert harness["image"] == "example.com/eval:v1"
    assert harness["imagePullPolicy"] == "IfNotPresent"
    assert report["image"] == "r:t"
    assert report["args"] == ["run-report"]
    assert report["env"] == harness["env"]
    helper = _defaults()["harness"]["helperResources"]
    assert report["resources"]["requests"] == {
        "cpu": str(helper["cpu"]),
        "memory": helper["memory"],
    }
    assert report["resources"]["limits"]["cpu"] == str(helper["cpuLimit"])
    mounts = {m["name"]: m["mountPath"] for m in report["volumeMounts"]}
    assert mounts == {"results": "/requests", "harness-scripts": "/workspace/harnesses"}
    assert pod["spec"]["shareProcessNamespace"] is True
    assert pod["metadata"]["annotations"] == {
        "kubectl.kubernetes.io/default-container": "harness"
    }


def test_plan_phase_shows_the_eval_pod_as_it_will_run() -> None:
    pod = _render_pod(EVAL_POD)
    harness, report = pod["spec"]["containers"]
    assert harness["image"] == "example.com/eval:v1"
    assert report["args"] == ["<built at run time>"]


def test_debug_and_summary_pods_have_no_sidecar() -> None:
    debug = _render_pod(EVAL_POD, report_command="")
    summary = _render_pod(
        EVAL_POD, pod_role=RUN_SUMMARY_ROLE, pod_resources=HELPER_RESOURCES
    )

    assert [c["image"] for c in debug["spec"]["containers"]] == ["example.com/eval:v1"]
    (container,) = summary["spec"]["containers"]
    assert container["image"] == "r:t"
    assert container["resources"]["requests"]["cpu"] == "100m"
    assert container["resources"]["limits"]["cpu"] == "1"
    labels = summary["metadata"]["labels"]
    assert labels["app"] == "llmdbench-harness-launcher"
    assert labels["function"] == "load_generator"
    assert labels[ROLE_LABEL] == RUN_SUMMARY_ROLE


# ---------------------------------------------------------------------------
# One treatment against a fake cluster
# ---------------------------------------------------------------------------


class _Applied:
    def __init__(self, success: bool = True, stdout: str = "") -> None:
        self.success = success
        self.stdout = stdout
        self.stderr = "" if success else "exceeded quota"


class _KubeCmd:
    def __init__(
        self, fail_summary_apply: bool = False, fail_task_applies: int = 0
    ) -> None:
        self.applied: list[str] = []
        self.fail_summary_apply = fail_summary_apply
        self.fail_task_applies = fail_task_applies

    def kube(self, *args: str, **_: Any) -> _Applied:
        if args[0] == "logs":
            return _Applied(stdout="WARNING: only 1 of 2 tasks finished")
        path = Path(args[2])
        self.applied.append(path.read_text(encoding="utf-8"))
        if f"-{RUN_SUMMARY_ROLE}-" in path.name:
            return _Applied(not self.fail_summary_apply)
        if self.fail_task_applies:
            self.fail_task_applies -= 1
            return _Applied(False)
        return _Applied()


def _spec(cmd: _KubeCmd, harness_name: str, plan_config: dict | None) -> _TreatmentSpec:
    return _TreatmentSpec(
        treatment=None,
        index=1,
        total=1,
        group=None,
        cmd=cmd,
        plan_config=(
            plan_config
            if plan_config is not None
            else {"harness": copy.deepcopy(EVAL_HARNESS)}
        ),
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
            "report: {{ 'sidecar' if report_command | default('') else '' }}\n"
            "command: {{ harness_command }}\n"
        ),
        pod_label="llmdbench-harness-launcher",
        parallelism=2,
        timeout=600,
    )


@pytest.fixture
def cluster(monkeypatch, tmp_path: Path):
    calls: dict[str, list] = {"waits": [], "sentinels": [], "pod_copies": []}
    deleted: list[str] = []
    wait_errors: dict[str, list[str]] = {}
    settled: list[bool] = []

    def _wait(_cmd, selector, _ns, timeout, _context, helper_containers=()):
        calls["waits"].append((selector, timeout, helper_containers))
        return wait_errors.get("summary" if "role=" in selector else "tasks", [])

    def _sentinels(_cmd, pods, _ns, path, _timeout, _context, container=None):
        calls["sentinels"].append((path, container))
        return []

    def _collect(*_a, harness_settled: bool = True, **_k):
        settled.append(harness_settled)
        return None, {}, []

    def _from_pods(*_a, container=None, **_k):
        calls["pod_copies"].append(container)
        return []

    monkeypatch.setattr(step07, "wait_for_pods_by_selector", _wait)
    monkeypatch.setattr(step07, "wait_for_harness_sentinels", _sentinels)
    monkeypatch.setattr(
        step07, "delete_pods_by_names", lambda _c, names, *_a: deleted.extend(names)
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        staticmethod(_collect),
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_from_pods",
        staticmethod(_from_pods),
    )

    def _run(
        cmd: _KubeCmd,
        harness_name: str = "eval-containers",
        plan_config: dict | None = None,
        **context_kwargs: Any,
    ):
        logger = _Logger()
        context = ExecutionContext(
            plan_dir=tmp_path, workspace=tmp_path, logger=logger, **context_kwargs
        )
        context.run_dir().mkdir(parents=True, exist_ok=True)
        spec = _spec(cmd, harness_name, plan_config)
        return DeployHarnessStep()._run_treatment(spec, context), logger

    _run.calls = calls
    _run.settled = settled
    _run.deleted = deleted
    _run.wait_errors = wait_errors
    return _run


def _docs(cmd: _KubeCmd) -> list[dict]:
    return [yaml.safe_load(text) for text in cmd.applied]


def test_treatment_runs_tasks_with_sidecars_then_the_summary(cluster) -> None:
    cmd = _KubeCmd()

    (succeeded, errors, deployed), _ = cluster(cmd)

    assert succeeded, errors
    assert deployed == 2
    tasks, summary = _docs(cmd)[:2], _docs(cmd)[2]
    for task in tasks:
        assert task["report"] == "sidecar"
        assert TASK_EXIT_MARKER in task["command"]
    assert summary["role"] == RUN_SUMMARY_ROLE
    assert summary["report"] is None
    assert summary["cpu"] == HELPER_RESOURCES["cpu"]
    assert "--expect 2 --deadline 630 /requests" in summary["command"]

    (task_wait, summary_wait) = cluster.calls["waits"]
    assert task_wait[0].endswith(f"{ROLE_LABEL}!={RUN_SUMMARY_ROLE}")
    assert task_wait[1:] == (600, (REPORT_CONTAINER,))
    assert summary_wait[0].endswith(f"{ROLE_LABEL}={RUN_SUMMARY_ROLE}")
    assert summary_wait[1] == RUN_SUMMARY_CONFIG["waitTimeout"]
    assert cluster.settled == [True]
    assert len(cluster.deleted) == 3


def test_unfinished_summary_only_warns_and_blocks_compression(cluster) -> None:
    cluster.wait_errors["summary"] = ["Pods did not complete within 30s"]

    (succeeded, errors, _), logger = cluster(_KubeCmd())

    assert succeeded, errors
    assert any("only 1 of 2 tasks finished" in w for w in logger.warnings)
    assert cluster.settled == [False]
    assert any(f"-{RUN_SUMMARY_ROLE}-" in name for name in cluster.deleted)


def test_failed_tasks_still_wait_for_the_summary(cluster) -> None:
    cluster.wait_errors["tasks"] = ["Found pods in error state: task exited 1"]

    (succeeded, _, _), _ = cluster(_KubeCmd())

    assert not succeeded
    assert len(cluster.calls["waits"]) == 2


def test_summary_expects_only_the_tasks_that_were_deployed(cluster) -> None:
    cmd = _KubeCmd(fail_task_applies=1)

    cluster(cmd)

    summary = next(d for d in _docs(cmd) if d["role"] == RUN_SUMMARY_ROLE)
    assert "--expect 1 " in summary["command"]


def test_no_summary_pod_when_no_task_was_deployed(cluster) -> None:
    cmd = _KubeCmd(fail_task_applies=2)

    (succeeded, _, _), _ = cluster(cmd)

    assert not succeeded
    assert all(d["role"] is None for d in _docs(cmd))
    assert cluster.calls["waits"] == []


@pytest.mark.parametrize("failure", ["render", "apply"])
def test_a_broken_summary_pod_only_warns(cluster, monkeypatch, failure: str) -> None:
    if failure == "render":
        real_render = DeployHarnessStep._render_template

        def _render(template: str, values: dict) -> str:
            if values.get("pod_role") == RUN_SUMMARY_ROLE:
                raise ValueError("bad template")
            return real_render(template, values)

        monkeypatch.setattr(
            DeployHarnessStep, "_render_template", staticmethod(_render)
        )
    cmd = _KubeCmd(fail_summary_apply=failure == "apply")

    (succeeded, errors, deployed), logger = cluster(cmd)

    assert succeeded, errors
    assert deployed == 2
    assert any("run summary pod" in w for w in logger.warnings)
    assert len(cluster.calls["waits"]) == 1


@pytest.mark.parametrize(
    ("harness", "key"),
    [
        ({k: v for k, v in EVAL_HARNESS.items() if k != "evalImage"}, "evalImage"),
        ({**EVAL_HARNESS, "evalImage": None}, "evalImage"),
        ({k: v for k, v in EVAL_HARNESS.items() if k != "helperResources"}, "helper"),
        ({k: v for k, v in EVAL_HARNESS.items() if k != "runSummary"}, "runSummary"),
    ],
)
def test_missing_config_fails_before_any_pod(cluster, harness: dict, key: str) -> None:
    cmd = _KubeCmd()

    with pytest.raises(KeyError, match=key):
        cluster(cmd, plan_config={"harness": copy.deepcopy(harness)})

    assert cmd.applied == []


def test_no_pvc_reads_the_sidecar_not_the_task(cluster) -> None:
    cmd = _KubeCmd()

    cluster(cmd, no_pvc=True)

    assert [d["role"] for d in _docs(cmd)] == [None, None]
    assert cluster.calls["sentinels"] == [
        (f"/requests/{HARNESS_DONE_SENTINEL}", REPORT_CONTAINER)
    ]
    assert cluster.calls["pod_copies"] == [REPORT_CONTAINER]


def test_other_harnesses_get_no_helpers(cluster) -> None:
    cmd = _KubeCmd()

    (succeeded, errors, _), _ = cluster(cmd, harness_name="inference-perf")

    assert succeeded, errors
    assert all(d["role"] is None and d["report"] is None for d in _docs(cmd))
    assert all(TASK_EXIT_MARKER not in d["command"] for d in _docs(cmd))
    assert cluster.calls["waits"][0][2] == ()


def test_a_failed_sidecar_does_not_fail_the_task(tmp_path: Path) -> None:
    def _container(name: str, code: int, reason: str) -> dict:
        return {
            "name": name,
            "state": {"terminated": {"exitCode": code, "reason": reason}},
        }

    pod = {
        "metadata": {"name": "p"},
        "status": {
            "phase": "Failed",
            "containerStatuses": [
                _container("harness", 0, "Completed"),
                _container(REPORT_CONTAINER, 137, "OOMKilled"),
            ],
        },
    }

    class _Cmd:
        def kube(self, *args: str, **_: Any) -> _Applied:
            if "jsonpath={.items[*].status.phase}" in args:
                return _Applied(stdout="Failed")
            return _Applied(stdout=json.dumps({"items": [pod]}))

    logger = _Logger()
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=logger)

    failing = kube_helpers.wait_for_pods_by_selector(_Cmd(), "s", "ns", 5, context)
    tolerant = kube_helpers.wait_for_pods_by_selector(
        _Cmd(), "s", "ns", 5, context, helper_containers=(REPORT_CONTAINER,)
    )

    assert failing and REPORT_CONTAINER in failing[0]
    assert tolerant == []
    assert any(REPORT_CONTAINER in w for w in logger.warnings)


@pytest.mark.parametrize("harness_log", ["task log", ""])
def test_sidecar_logs_are_captured_next_to_the_task_log(
    tmp_path: Path, harness_log: str
) -> None:
    class _Cmd:
        def kube(self, *args: str, **_: Any) -> _Applied:
            return _Applied(stdout="report log" if "-c" in args else harness_log)

    logger = _Logger()
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=logger)

    kube_helpers.capture_pod_logs(
        _Cmd(), ["p"], "ns", tmp_path, context, extra_containers=(REPORT_CONTAINER,)
    )

    assert (tmp_path / f"p-{REPORT_CONTAINER}.log").read_text() == "report log"
    assert (tmp_path / "p.log").exists() is bool(harness_log)
    assert bool(logger.warnings) is not bool(harness_log)


# ---------------------------------------------------------------------------
# nok8s
# ---------------------------------------------------------------------------


def _nok8s(tmp_path: Path, harness: str, connection: str = "", replies=None, **ctx):
    from test_nok8s_harness_status import (
        DeployHarnessLocalStep,
        _Command,
        _context,
        _plan_config,
        _Result,
    )

    base = {" inspect ": _Result(stdout="exited 0\n")}
    if connection:
        base["printenv HOME"] = _Result(stdout="/home/bench\n")
    cmd = _Command({**(replies or {}), **base})
    logger = _Logger()
    context, stack_path = _context(tmp_path, cmd, logger)
    context.harness_parallelism = 2
    for key, value in ctx.items():
        setattr(context, key, value)
    config = _plan_config()
    config["harness"].update({"name": harness, **copy.deepcopy(EVAL_HARNESS)})
    if connection:
        config["nok8s"]["connection"] = connection
    (stack_path / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    return DeployHarnessLocalStep().execute(context, stack_path), cmd, logger


def _runs(cmd, kind: str = "") -> list[str]:
    runs = [c for c in cmd.commands if " run -d --name " in c]
    if kind == "task":
        return [r for r in runs if not re.search(r"-(report|summary) ", r)]
    return [r for r in runs if f"-{kind} " in r] if kind else runs


def _image(run: str) -> str:
    return run.split("--entrypoint sh ", 1)[1].split()[0]


@pytest.mark.parametrize("no_pvc", [False, True])
def test_nok8s_tasks_get_report_containers_and_a_summary(
    tmp_path: Path, no_pvc: bool
) -> None:
    result, cmd, _ = _nok8s(tmp_path, "eval-containers", no_pvc=no_pvc)

    assert result.success, result.errors
    tasks, reports, summary = (
        _runs(cmd, "task"),
        _runs(cmd, "report"),
        _runs(cmd, "summary"),
    )
    assert (len(tasks), len(reports), len(summary)) == (2, 2, 1)
    assert {_image(r) for r in tasks} == {"example.com/eval:v1"}
    assert {_image(r) for r in reports + summary} == {"example.com/bench:latest"}
    analyzer = (
        REPO / "llmdbenchmark/analysis/scripts/eval-containers-analyze_results.sh"
    )
    assert all(f"-v {analyzer}:/tmp/{analyzer.name}:ro " in r for r in reports)

    entries = {p.name: p.read_text() for p in tmp_path.rglob(".harness-entry-*.sh")}
    task_entries = [
        t for n, t in entries.items() if not re.search(r"-(report|summary)\.sh$", n)
    ]
    report_entries = [t for n, t in entries.items() if n.endswith("-report.sh")]
    assert len(task_entries) == 2 and all(TASK_EXIT_MARKER in t for t in task_entries)
    assert len(report_entries) == 2
    assert all("/proc/" not in t and "[ 0 -gt 0 ]" in t for t in report_entries)
    summary_entry = next(t for n, t in entries.items() if n.endswith("-summary.sh"))
    assert f"/tmp/{RUN_SUMMARY_SCRIPT.name} --expect 2 " in summary_entry

    waits = [c for c in cmd.commands if " wait " in c]
    assert len(waits) == 3
    assert waits[1].count("-report") == 2 and "-summary" in waits[2]
    assert all(
        w.startswith(f"timeout {RUN_SUMMARY_CONFIG['waitTimeout']} ") for w in waits[1:]
    )
    marks = [c for c in cmd.commands if "|| echo 137 > " in c]
    assert len(marks) == 2
    assert cmd.commands.index(marks[-1]) < cmd.commands.index(waits[1])


def test_nok8s_failed_staging_skips_reports_and_summary(tmp_path: Path) -> None:
    from test_nok8s_harness_status import _Result

    result, cmd, logger = _nok8s(
        tmp_path,
        "eval-containers",
        "ssh://bench@10.0.0.7",
        replies={"eval-containers-analyze_results.sh": _Result(exit_code=1)},
    )

    assert result.success, result.errors
    assert len(_runs(cmd, "task")) == 2
    assert _runs(cmd, "report") == [] and _runs(cmd, "summary") == []
    assert sum("No benchmark reports" in w for w in logger.warnings) == 1


def test_nok8s_unfinished_helper_only_warns(tmp_path: Path) -> None:
    from test_nok8s_harness_status import _Result

    result, _, logger = _nok8s(
        tmp_path,
        "eval-containers",
        replies={"-summary": _Result(stdout="running\n")},
    )

    assert result.success, result.errors
    assert any("did not finish" in w for w in logger.warnings)


def test_nok8s_remote_pushes_the_helper_scripts(tmp_path: Path) -> None:
    result, cmd, _ = _nok8s(tmp_path, "eval-containers", "ssh://bench@10.0.0.7")

    assert result.success, result.errors
    remote_root = f"/home/bench/.llmdbench/nok8s-runs/stack/{tmp_path.name}"
    for name, kind in (
        (RUN_SUMMARY_SCRIPT.name, "summary"),
        ("eval-containers-analyze_results.sh", "report"),
    ):
        target = f"{remote_root}/{name}"
        push = next(
            i for i, c in enumerate(cmd.commands) if target in c and "scp " in c
        )
        launch = cmd.commands.index(_runs(cmd, kind)[0])
        assert push < launch
        assert f"-v {target}:/tmp/{name}:ro " in cmd.commands[launch]


def test_nok8s_other_harnesses_get_no_helpers(tmp_path: Path) -> None:
    result, cmd, _ = _nok8s(tmp_path, "inference-perf")

    assert result.success, result.errors
    assert len(_runs(cmd)) == 2
    assert not any(re.search(r"-(report|summary)|echo 137", c) for c in cmd.commands)


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


# ---------------------------------------------------------------------------
# Driver fallback
# ---------------------------------------------------------------------------


def test_nop_counts_the_analysis_collection_moved_away(tmp_path: Path) -> None:
    from llmdbenchmark.analysis import pod_analysis_present

    results = tmp_path / "results" / "exp_1"
    results.mkdir(parents=True)
    moved = tmp_path / "analysis" / "exp_1"
    moved.mkdir(parents=True)

    assert not pod_analysis_present("nop", results, tmp_path / "analysis")
    (moved / "result.txt").write_text("ok\n", encoding="utf-8")
    assert pod_analysis_present("nop", results, tmp_path / "analysis")


@pytest.mark.parametrize(
    ("archived", "warns"), [({}, False), ({"stage_0.json": b"{}"}, True)]
)
def test_only_archived_raw_results_are_reported(
    tmp_path: Path, monkeypatch, archived: dict, warns: bool
) -> None:
    from llmdbenchmark import analysis

    monkeypatch.setattr(analysis, "read_members", lambda *_a: archived)

    error = analysis.run_analysis("inference-perf", tmp_path)

    assert bool(error) is warns
