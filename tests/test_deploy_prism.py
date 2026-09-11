"""Tests for the persistent in-cluster llm-d-prism dashboard.

Behavior under test:
- ``--prism`` / ``--no-prism`` override ``prism.enabled``; omitting both keeps
  scenario/defaults values.
- ``should_skip`` honours ``prism.enabled`` and the nok8s / kustomize-skip-infra
  deployment methods.
- ``execute`` is non-fatal: an apply failure or an unready pod still succeeds.
- The OpenShift route is created only when missing, and only on OpenShift.
- Normal teardown preserves persist-labelled resources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.standup.steps.step_10_deploy_prism import DeployPrismStep
from llmdbenchmark.teardown.steps.step_03_delete_resources import DeleteResourcesStep


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    """Records kube invocations; canned replies keyed by leading args."""

    replies: dict[tuple[str, ...], _StubResult] = field(default_factory=dict)
    pods_ready: bool = True
    calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.calls.append(args)
        for key, reply in self.replies.items():
            if args[: len(key)] == key:
                return reply
        return _StubResult()

    def wait_for_pods(self, **_: Any) -> _StubResult:
        self.calls.append(("wait_for_pods",))
        return _StubResult(success=self.pods_ready, stderr="timeout")


@dataclass
class _StubContext:
    rendered_stacks: list[Path] = field(default_factory=list)
    deployed_methods: list[str] = field(default_factory=lambda: ["modelservice"])
    kustomize_skip_infra: bool = True
    is_openshift: bool = False
    dry_run: bool = False
    cmd: _StubCmd = field(default_factory=_StubCmd)
    logger: Any = field(default_factory=MagicMock)

    def require_cmd(self) -> _StubCmd:
        return self.cmd

    def require_namespace(self) -> str:
        return "ns1"


def _stack(
    tmp_path: Path, *, prism: dict | None = None, rendered: str = "kind: X\n"
) -> Path:
    """Create a rendered-stack directory with config.yaml and 35_prism.yaml."""
    d = tmp_path / "stack-a"
    d.mkdir(parents=True, exist_ok=True)
    cfg: dict[str, Any] = {"namespace": {"name": "ns1"}}
    if prism is not None:
        cfg["prism"] = prism
    (d / "config.yaml").write_text(yaml.safe_dump(cfg))
    (d / "35_prism.yaml").write_text(rendered)
    return d


def _verbs(cmd: _StubCmd, verb: str) -> list[tuple]:
    return [args for args in cmd.calls if args[0] == verb]


@pytest.mark.parametrize(
    "cli_prism,scenario,expected",
    [
        (None, False, False),  # no flag: scenario value untouched
        (True, False, True),  # --prism forces on
        (False, True, False),  # --no-prism forces off
    ],
)
def test_resolve_prism_override(
    cli_prism: bool | None, scenario: bool, expected: bool
) -> None:
    renderer = RenderPlans.__new__(RenderPlans)
    renderer.logger = MagicMock()
    renderer.cli_prism = cli_prism

    result = renderer._resolve_prism({"prism": {"enabled": scenario}})

    assert result["prism"]["enabled"] is expected


@pytest.mark.parametrize(
    "prism,methods,skip_infra,expected",
    [
        (None, ["modelservice"], True, False),  # absent config => deployed
        ({"enabled": False}, ["modelservice"], True, True),
        ({"enabled": True}, ["nok8s"], True, True),
        ({"enabled": True}, ["kustomize"], True, True),
        ({"enabled": True}, ["kustomize"], False, False),
    ],
)
def test_should_skip(
    tmp_path: Path,
    prism: dict | None,
    methods: list[str],
    skip_infra: bool,
    expected: bool,
) -> None:
    ctx = _StubContext(
        rendered_stacks=[_stack(tmp_path, prism=prism)],
        deployed_methods=methods,
        kustomize_skip_infra=skip_infra,
    )

    assert DeployPrismStep().should_skip(ctx) is expected


def test_execute_applies_rendered_manifest(tmp_path: Path) -> None:
    ctx = _StubContext(rendered_stacks=[_stack(tmp_path)])

    result = DeployPrismStep().execute(ctx)

    assert result.success
    assert _verbs(ctx.cmd, "apply")[0][-1].endswith("35_prism.yaml")
    assert _verbs(ctx.cmd, "wait_for_pods")


def test_execute_dry_run_touches_no_cluster(tmp_path: Path) -> None:
    ctx = _StubContext(rendered_stacks=[_stack(tmp_path)], dry_run=True)

    assert DeployPrismStep().execute(ctx).success
    assert ctx.cmd.calls == []


def test_execute_empty_manifest_is_a_noop(tmp_path: Path) -> None:
    """A disabled template renders empty; there is nothing to apply."""
    ctx = _StubContext(rendered_stacks=[_stack(tmp_path, rendered="")])

    assert DeployPrismStep().execute(ctx).success
    assert _verbs(ctx.cmd, "apply") == []


def test_execute_apply_failure_is_non_fatal(tmp_path: Path) -> None:
    ctx = _StubContext(rendered_stacks=[_stack(tmp_path)])
    ctx.cmd.replies[("apply",)] = _StubResult(success=False, stderr="forbidden")

    assert DeployPrismStep().execute(ctx).success
    assert _verbs(ctx.cmd, "wait_for_pods") == []


def test_execute_unready_pod_is_non_fatal(tmp_path: Path) -> None:
    ctx = _StubContext(rendered_stacks=[_stack(tmp_path)])
    ctx.cmd.pods_ready = False

    assert DeployPrismStep().execute(ctx).success


def test_execute_creates_route_on_openshift(tmp_path: Path) -> None:
    ctx = _StubContext(rendered_stacks=[_stack(tmp_path)], is_openshift=True)

    DeployPrismStep().execute(ctx)

    # A numeric --port makes the route 503; it must name the service port.
    assert "--port=http" in _verbs(ctx.cmd, "expose")[0]


@pytest.mark.parametrize(
    "openshift,prism,existing",
    [
        (False, None, ""),  # vanilla k8s: no route
        (True, {"enabled": True, "route": {"enabled": False}}, ""),
        (True, None, "route.route.openshift.io/llm-d-prism\n"),  # already exists
    ],
)
def test_execute_skips_route(
    tmp_path: Path, openshift: bool, prism: dict | None, existing: str
) -> None:
    ctx = _StubContext(
        rendered_stacks=[_stack(tmp_path, prism=prism)], is_openshift=openshift
    )
    ctx.cmd.replies[("get", "route")] = _StubResult(stdout=existing)

    DeployPrismStep().execute(ctx)

    assert _verbs(ctx.cmd, "expose") == []


def test_prism_protected_names(tmp_path: Path) -> None:
    cmd = _StubCmd(
        replies={
            ("get",): _StubResult(stdout="deployment.apps/llm-d-prism\nservice/p\n")
        }
    )

    protected = DeleteResourcesStep._prism_protected_names(
        cmd, "deployment,service", "ns1"
    )

    assert protected == {"deployment.apps/llm-d-prism", "service/p"}
    assert any("llm-d-benchmark.ai/persist=true" in args for args in cmd.calls)
