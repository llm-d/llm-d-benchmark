"""Coordinator phase and README monitoring commands in step_05_kustomize_deploy.

llm-d's coord-disaggregation guide deploys the router Coordinator in its own
README step ("Deploy the Coordinator"), after the model servers, and lists the
Coordinator's PodMonitor under "Enable monitoring". Without a Coordinator
heading rule the Coordinator commands fall into the model-server section, where
only the one per-backend model-server command is applied, and the README's
monitoring commands are parsed but never run.
"""

from __future__ import annotations

import sys
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

# Stub the `planner` package that llmdbenchmark.standup.steps
# transitively imports (same as tests/test_kustomize_autoscaling.py).
if "planner.capacity_planner" not in sys.modules:

    class _PermissiveModule(types.ModuleType):
        def __getattr__(self, name: str):  # type: ignore[override]
            return type(name, (), {})

    _planner = _PermissiveModule("planner")
    _planner.__file__ = "<planner stub>"
    _capacity = _PermissiveModule("planner.capacity_planner")
    _capacity.__file__ = "<planner stub>"
    sys.modules.setdefault("planner", _planner)
    sys.modules["planner.capacity_planner"] = _capacity

from llmdbenchmark.kustomize.readme_parser import CommandPhase, parse_guide_readme
from llmdbenchmark.standup.steps.step_05_kustomize_deploy import KustomizeDeployStep

GUIDE = "test-guide"

# The shape of guides/coord-disaggregation.
README = """\
# Coordinator Disaggregation (Encode / Prefill / Decode)

## Overview

### When to use the Coordinator

```bash
kubectl get pods -n ${NAMESPACE}
```

## Installation Instructions

### 2. Deploy the Model Servers

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/modelserver/
```

### 3. Deploy the Coordinator

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/coordinator/base
```

### 4. Enable Autoscaling (KEDA)

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/keda
```

### 5. (Optional) Enable monitoring

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/coordinator/monitoring
```

## Verification

```bash
kubectl get pods -n ${NAMESPACE}
```
"""


def _write_guide(root: Path, body: str = README) -> Path:
    guide_dir = root / "guides" / GUIDE
    guide_dir.mkdir(parents=True, exist_ok=True)
    readme = guide_dir / "README.md"
    readme.write_text(textwrap.dedent(body), encoding="utf-8")
    return readme


def _commands(parsed, phase: CommandPhase) -> list[str]:
    return [gc.raw for gc in parsed.get_commands(phase)]


# --- parser -----------------------------------------------------------------


def test_coordinator_section_gets_its_own_phase(tmp_path: Path):
    parsed = parse_guide_readme(_write_guide(tmp_path))

    assert _commands(parsed, CommandPhase.COORDINATOR) == [
        "kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/coordinator/base"
    ]
    assert _commands(parsed, CommandPhase.MODELSERVER) == [
        "kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/modelserver/"
    ]


def test_coordinator_title_and_overview_do_not_route_preamble_commands(
    tmp_path: Path,
):
    readme = _write_guide(
        tmp_path,
        """\
        # Coordinator Disaggregation

        ## Overview

        ### When to use the Coordinator

        ```bash
        kubectl get nodes
        ```

        ## Prerequisites
        """,
    )

    assert parse_guide_readme(readme).commands == []


def test_router_coordinator_heading_is_coordinator_not_router(tmp_path: Path):
    # The shape of guides/batch-serving/asynchronous-processing/multitenant.
    readme = _write_guide(
        tmp_path,
        """\
        # Async Multitenant

        ## Installation

        ### 1. Deploy the router

        ```bash
        helm install router oci://charts/router -n ${NAMESPACE}
        ```

        ### 4. (Optional) HTTP front door with the router coordinator

        ```bash
        kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/manifests/coordinator
        ```
        """,
    )

    parsed = parse_guide_readme(readme)

    assert _commands(parsed, CommandPhase.ROUTER) == [
        "helm install router oci://charts/router -n ${NAMESPACE}"
    ]
    assert _commands(parsed, CommandPhase.COORDINATOR) == [
        "kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/manifests/coordinator"
    ]


# --- deploy step -------------------------------------------------------------


class _RecordingCmd:
    """Records kube calls and the pod wait, in order; fails selected calls."""

    dry_run = False

    def __init__(self, fail_on: str = ""):
        self.events: list[str] = []
        self.fail_on = fail_on

    def kube(self, *args, **_kw):
        line = " ".join(args)
        self.events.append(line)
        failed = bool(self.fail_on) and self.fail_on in line
        return SimpleNamespace(success=not failed, stdout="", stderr="boom")

    def wait_for_pods(self, **_kw):
        self.events.append("wait_for_pods")
        return SimpleNamespace(success=True, stdout="", stderr="")


@pytest.fixture
def deploy(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    stack = tmp_path / "stack"
    stack.mkdir()
    for helper in ("_ensure_hf_token_secret", "_propagate_standup_parameters"):
        monkeypatch.setattr(KustomizeDeployStep, helper, lambda *a, **k: None)

    def _deploy(cmd: _RecordingCmd, body: str = README, **kustomize):
        config = {
            "enabled": True,
            "guideName": GUIDE,
            "repoPath": str(repo),
            "deployTimeout": 10,
            **kustomize,
        }
        (stack / "config.yaml").write_text(
            yaml.safe_dump({"kustomize": config}), encoding="utf-8"
        )
        _write_guide(repo, body)
        ctx = MagicMock(harness_namespace=None, deployed_endpoints={})
        ctx.require_cmd.return_value = cmd
        ctx.require_namespace.return_value = "ns"
        return KustomizeDeployStep().execute(ctx, stack)

    return _deploy


def _index(events: list[str], marker: str) -> int:
    return next(i for i, e in enumerate(events) if marker in e)


def test_coordinator_runs_after_the_pod_wait_and_before_autoscaling(deploy):
    cmd = _RecordingCmd()

    result = deploy(cmd)

    assert result.success, result.errors
    order = [
        _index(cmd.events, m)
        for m in ("wait_for_pods", "coordinator/base", "test-guide/keda")
    ]
    assert order == sorted(order)


def test_coordinator_failure_fails_the_step(deploy):
    result = deploy(_RecordingCmd(fail_on="coordinator/base"))

    assert not result.success
    assert result.message == "Coordinator deployment failed"
    assert "boom" in result.errors[0]


def test_coordinator_command_whose_guard_does_not_hold_is_skipped(deploy):
    guarded = README.replace(
        "### 3. Deploy the Coordinator\n\n```bash\n",
        "### 3. Deploy the Coordinator\n\n```bash\n# only when PROVIDER_NAME=gke:\n",
    ).replace(
        "## Overview\n",
        "## Overview\n\n```bash\nexport PROVIDER_NAME=istio\n```\n",
    )
    cmd = _RecordingCmd()

    result = deploy(cmd, guarded)

    assert result.success, result.errors
    assert not any("coordinator/base" in e for e in cmd.events)


@pytest.mark.parametrize(("monitoring", "applied"), [(True, True), (False, False)])
def test_readme_monitoring_commands_run_only_when_monitoring_is_enabled(
    deploy, monitoring: bool, applied: bool
):
    cmd = _RecordingCmd()

    result = deploy(cmd, monitoring=monitoring)

    assert result.success, result.errors
    assert any("coordinator/monitoring" in e for e in cmd.events) is applied


def test_readme_monitoring_failure_does_not_fail_the_step(deploy):
    result = deploy(_RecordingCmd(fail_on="coordinator/monitoring"), monitoring=True)

    assert result.success, result.errors
