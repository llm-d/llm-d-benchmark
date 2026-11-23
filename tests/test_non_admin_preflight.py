"""A non-admin standup finds permission gaps one failure at a time, deep
into the run. The preflight asks the cluster up front, as the invoking
user, and prints the remediation for every gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.standup.steps.step_01_non_admin_preflight import (
    _CHART_GRANTED_RESOURCES,
    NonAdminPreflightStep,
)

_MODEL = "qwen-qwe-6ed7b204-wen3-32b"
_POOL = "inferencepools.inference.networking.k8s.io"


class _StubLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def log_error(self, msg: str) -> None:
        self.errors.append(msg)

    def line_break(self) -> None:
        pass

    def set_indent(self, _level: int) -> None:
        pass


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    missing_namespaces: set[str] = field(default_factory=set)
    missing_api_group: bool = False
    api_fails_with_output: bool = False
    denial_output: str = "no"
    denied: set[tuple[str, str]] = field(default_factory=set)
    failing_probes: set[tuple[str, str]] = field(default_factory=set)
    probed: list[tuple[str, str]] = field(default_factory=list)
    checked_namespaces: list[str] = field(default_factory=list)
    api_queried: bool = False

    def kube(self, *args: str, **kw: Any) -> _StubResult:
        if args[0] == "get" and args[1] == "namespace":
            self.checked_namespaces.append(args[2])
            return _StubResult(success=args[2] not in self.missing_namespaces)
        if args[0] == "api-resources":
            self.api_queried = True
            if self.missing_api_group:
                return _StubResult()
            stdout = f"{_POOL}\n"
            # discovery of an unrelated group can fail the whole command
            if self.api_fails_with_output:
                return _StubResult(success=False, stdout=stdout)
            return _StubResult(stdout=stdout)
        if args[0] == "auth" and args[1] == "can-i":
            self.probed.append((args[3], kw.get("namespace")))
            if (args[2], args[3]) in self.failing_probes:
                return _StubResult(success=False)
            # can-i exits 1 on "no", so the real result carries both
            if (args[2], args[3]) in self.denied:
                return _StubResult(success=False, stdout=self.denial_output)
            return _StubResult(stdout="yes")
        return _StubResult()


@dataclass
class _StubContext:
    rendered_stacks: list[Path]
    cmd: _StubCmd
    non_admin: bool = True
    dry_run: bool = False
    deployed_methods: list[str] = field(default_factory=lambda: ["modelservice"])
    is_openshift: bool = True
    namespace: str | None = None
    harness_namespace: str | None = None
    logger: _StubLogger = field(default_factory=_StubLogger)

    def require_cmd(self) -> _StubCmd:
        return self.cmd


def _stack(tmp_path: Path, cfg: dict | None = None, name: str = "s1") -> Path:
    stack = tmp_path / name
    stack.mkdir(parents=True)
    base = {
        "namespace": {"name": "nikita-dev"},
        "model_id_label": _MODEL,
        "gateway": {"className": "epponly"},
        "modelservice": {"enabled": True},
    }
    (stack / "config.yaml").write_text(
        yaml.safe_dump({**base, **(cfg or {})}), encoding="utf-8"
    )
    return stack


def _run(tmp_path: Path, cmd: _StubCmd, cfg: dict | None = None):
    context = _StubContext(rendered_stacks=[_stack(tmp_path, cfg)], cmd=cmd)
    result = NonAdminPreflightStep().execute(context)
    return result, context.logger


def _run_stacks(stacks: list[Path], cmd: _StubCmd):
    context = _StubContext(rendered_stacks=stacks, cmd=cmd)
    return NonAdminPreflightStep().execute(context), context.logger


def test_all_checks_pass(tmp_path):
    result, logger = _run(tmp_path, _StubCmd())

    assert result.success
    assert logger.errors == []


@pytest.mark.parametrize(
    "cmd_kwargs,expected",
    [
        ({"missing_namespaces": {"nikita-dev"}}, "does not exist"),
        ({"denied": {("create", "rolebindings")}}, "namespace-admin"),
        ({"denied": {("list", _POOL)}}, "ask an admin"),
        # the chart role grants get, list and watch; one missing verb
        # is enough to fail the escalation check
        ({"denied": {("watch", _POOL)}}, "ask an admin"),
        ({"missing_api_group": True}, "inferencepools CRD"),
    ],
)
def test_every_gap_is_an_error_with_a_remediation(tmp_path, cmd_kwargs, expected):
    result, logger = _run(tmp_path, _StubCmd(**cmd_kwargs))

    assert not result.success
    assert any(expected in e for e in logger.errors)


@pytest.mark.parametrize("failing", [("create", "rolebindings"), ("list", _POOL)])
def test_failed_probe_is_an_error_not_a_pass(tmp_path, failing):
    cmd = _StubCmd(failing_probes={failing})

    result, logger = _run(tmp_path, cmd)

    assert not result.success
    assert any("could not ask" in e for e in logger.errors)


def test_denial_with_a_reason_is_still_a_denial(tmp_path):
    cmd = _StubCmd(
        denied={("list", _POOL)},
        denial_output="no - forbidden by RBAC",
    )

    result, logger = _run(tmp_path, cmd)

    assert not result.success
    assert any("ask an admin" in e for e in logger.errors)
    assert not any("could not ask" in e for e in logger.errors)


def test_a_line_before_the_answer_does_not_read_as_an_error(tmp_path):
    cmd = _StubCmd()
    cmd.denial_output = "yes"
    original = cmd.kube

    def _noisy(*args, **kw):
        result = original(*args, **kw)
        if args[0] == "auth":
            result.stdout = f"Warning: deprecated\n{result.stdout}"
        return result

    cmd.kube = _noisy

    result, logger = _run(tmp_path, cmd)

    assert result.success
    assert logger.errors == []


def test_api_group_answer_on_stdout_wins_over_exit_code(tmp_path):
    cmd = _StubCmd(api_fails_with_output=True)

    result, logger = _run(tmp_path, cmd)

    assert result.success
    assert logger.errors == []


def test_permission_probes_skip_the_harness_namespace(tmp_path):
    # the harness namespace has its own owner; standup creates nothing
    # there, so no check demands it
    cfg = {"harness": {"namespace": "nikita-harness"}}
    cmd = _StubCmd()

    _result, _logger = _run(tmp_path, cmd, cfg)

    assert {ns for _resource, ns in cmd.probed} == {"nikita-dev"}
    assert set(cmd.checked_namespaces) == {"nikita-dev"}


def test_chart_probes_target_the_gateway_namespace(tmp_path):
    # the helmfile declares every release in the gateway namespace
    cmd = _StubCmd()
    cfg = {"gateway": {"namespace": "gw-ns"}}

    _result, _logger = _run(tmp_path, cmd, cfg)

    chart_ns = {ns for resource, ns in cmd.probed if resource != "rolebindings"}
    assert chart_ns == {"gw-ns"}
    # the chart creates its rolebindings there, so admin rights are
    # demanded there too
    assert ("rolebindings", "gw-ns") in cmd.probed
    assert set(cmd.checked_namespaces) == {"nikita-dev", "gw-ns"}


def test_router_checks_skip_non_router_stacks(tmp_path):
    # standalone stacks deploy no router chart, and a className of none
    # deploys none either
    scenarios = [
        {
            "standalone": {"enabled": True},
            "modelservice": {"enabled": False},
            "vllmCommon": {"securityContext": {"runAsUser": 0}},
        },
        {"gateway": {"className": "none"}},
    ]
    for index, cfg in enumerate(scenarios):
        cmd = _StubCmd()

        result, logger = _run(tmp_path / f"case{index}", cmd, cfg)

        assert result.success
        probed_resources = {resource for resource, _ns in cmd.probed}
        assert not probed_resources.intersection(_CHART_GRANTED_RESOURCES)
        assert not cmd.api_queried
        assert logger.warnings == []


def test_standalone_method_skips_the_router_checks_and_warning(tmp_path):
    # a scenario can leave modelservice.enabled at its default true
    # while the deploy method is standalone; step 08 keys on the method
    cfg = {
        "standalone": {"enabled": True},
        "vllmCommon": {"securityContext": {"runAsUser": 0}},
    }
    cmd = _StubCmd()
    context = _StubContext(rendered_stacks=[_stack(tmp_path, cfg)], cmd=cmd)
    context.deployed_methods = ["standalone"]

    result = NonAdminPreflightStep().execute(context)

    assert result.success
    probed_resources = {resource for resource, _ns in cmd.probed}
    assert not probed_resources.intersection(_CHART_GRANTED_RESOURCES)
    assert context.logger.warnings == []


def test_standalone_stack_gateway_namespace_is_not_probed(tmp_path):
    stacks = [
        _stack(
            tmp_path,
            {
                "namespace": {"name": "ns-a"},
                "standalone": {"enabled": True},
                "modelservice": {"enabled": False},
            },
            name="s1",
        ),
        _stack(tmp_path, {"namespace": {"name": "ns-b"}}, name="s2"),
    ]
    cmd = _StubCmd()

    NonAdminPreflightStep().execute(_StubContext(rendered_stacks=stacks, cmd=cmd))

    chart_ns = {ns for resource, ns in cmd.probed if resource != "rolebindings"}
    assert chart_ns == {"ns-b"}


def _elevated(role: str) -> dict:
    return {
        role: {
            "extraContainerConfig": {
                "securityContext": {"runAsUser": 0, "runAsGroup": 0}
            }
        }
    }


def test_elevation_warning_names_the_overridden_service_account(tmp_path):
    # one config exercises the detection breadth: a top-level
    # vllmCommon context with runAsGroup 0, and an overridden SA
    cfg = {
        "vllmCommon": {"securityContext": {"runAsGroup": 0}},
        "serviceAccountOverride": "my-sa",
    }

    _result, logger = _run(tmp_path, _StubCmd(), cfg)

    assert len(logger.warnings) == 1
    warning = logger.warnings[0]
    assert "my-sa" in warning
    assert "runAsUser/runAsGroup 0" in warning
    assert "oc adm policy add-scc-to-user privileged -z my-sa -n nikita-dev" in warning


def test_elevation_warning_is_per_stack(tmp_path):
    stacks = [
        _stack(
            tmp_path, {**_elevated("decode"), "model_id_label": "sa-one"}, name="s1"
        ),
        _stack(
            tmp_path, {**_elevated("prefill"), "model_id_label": "sa-two"}, name="s2"
        ),
        _stack(tmp_path, {"namespace": {"name": "ns-b"}}, name="s3"),
    ]

    _result, logger = _run_stacks(stacks, _StubCmd())

    assert len(logger.warnings) == 2
    joined = "\n".join(logger.warnings)
    assert "sa-one" in joined and "sa-two" in joined
    assert "ns-b" not in joined


def test_direct_service_warning_names_the_gateway_namespace(tmp_path):
    cfg = {
        "decode": {"extraContainerConfig": {"securityContext": {"runAsUser": 0}}},
        "gateway": {"className": "none", "namespace": "direct-ns"},
    }

    _result, logger = _run(tmp_path, _StubCmd(), cfg)

    assert any("direct-ns" in w for w in logger.warnings)


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {"decode": {"extraContainerConfig": None}},
        {"decode": {"extraContainerConfig": {"securityContext": {"runAsUser": 1000}}}},
    ],
)
def test_nothing_elevated_stays_quiet(tmp_path, cfg):
    _result, logger = _run(tmp_path, _StubCmd(), cfg)

    assert logger.warnings == []


def test_missing_plan_config_skips_the_scc_warning(tmp_path):
    stack = tmp_path / "s1"
    stack.mkdir()
    context = _StubContext(
        rendered_stacks=[stack], cmd=_StubCmd(), namespace="nikita-dev"
    )

    result = NonAdminPreflightStep().execute(context)

    assert result.success


def test_executor_honors_the_skip_and_the_oc_guard(tmp_path, monkeypatch):
    from llmdbenchmark.executor import step_executor

    def _context(name: str, **kwargs):
        context = _StubContext(
            rendered_stacks=[_stack(tmp_path / name)], cmd=_StubCmd(), **kwargs
        )
        context.resolve_cluster = lambda: None
        context._cluster_resolved = True
        context.current_phase = NonAdminPreflightStep().phase
        return context

    dry_run = _context("dry", dry_run=True)
    result = step_executor.StepExecutor(
        [NonAdminPreflightStep()], dry_run, dry_run.logger
    ).execute()
    assert not result.errors
    assert result.global_results[0].message == "Skipped"
    assert dry_run.cmd.probed == []

    monkeypatch.setattr(step_executor.shutil, "which", lambda _name: None)
    live = _context("live")
    result = step_executor.StepExecutor(
        [NonAdminPreflightStep()], live, live.logger
    ).execute()
    assert result.errors
    assert live.cmd.probed == []


def test_step_skips_without_non_admin_or_in_dry_run(tmp_path):
    step = NonAdminPreflightStep()

    assert step.should_skip(
        _StubContext(rendered_stacks=[], cmd=_StubCmd(), non_admin=False)
    )
    assert step.should_skip(
        _StubContext(rendered_stacks=[], cmd=_StubCmd(), dry_run=True)
    )
    assert step.should_skip(
        _StubContext(rendered_stacks=[], cmd=_StubCmd(), deployed_methods=["nok8s"])
    )
