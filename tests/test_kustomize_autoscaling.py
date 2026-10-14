"""Tests for the kustomize AUTOSCALING phase: README routing of a guide's
autoscaling (e.g. KEDA) step, and running it after the pod wait."""

from __future__ import annotations

import sys
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

# See test_kustomize_hf_token.py: stub the `planner` package that
# llmdbenchmark.standup.steps transitively imports.
if "planner.capacity_planner" not in sys.modules:

    class _PermissiveModule(types.ModuleType):
        def __getattr__(self, name: str):  # type: ignore[override]
            return type(name, (), {})

    sys.modules.setdefault("planner", _PermissiveModule("planner"))
    sys.modules["planner.capacity_planner"] = _PermissiveModule(
        "planner.capacity_planner"
    )

from llmdbenchmark.kustomize.readme_parser import CommandPhase, parse_guide_readme
from llmdbenchmark.standup.steps.step_05_kustomize_deploy import KustomizeDeployStep

GUIDE = "test-guide"
KEDA_HEADING = "Enable Scale-from-Zero Autoscaling (KEDA)"

# The shape of guides/fast-model-actuation/fast-model-actuation-keda.
README = f"""\
# Test Guide + KEDA Autoscaling

### 5. Deploy the Model Server

```bash
kubectl apply -n ${{NAMESPACE}} -k ${{REPO_ROOT}}/guides/test-guide/modelserver/
```

### 6. {KEDA_HEADING}

```bash
kubectl apply -n ${{NAMESPACE}} -k ${{REPO_ROOT}}/guides/test-guide/keda/ocp
kubectl wait --for=condition=Ready scaledobject/${{GUIDE_NAME}}-queue
```

## Verification

```bash
kubectl get pods -n ${{NAMESPACE}}
```
"""


def _write_guide(root: Path, body: str = README) -> Path:
    guide_dir = root / "guides" / GUIDE
    guide_dir.mkdir(parents=True)
    readme = guide_dir / "README.md"
    readme.write_text(textwrap.dedent(body), encoding="utf-8")
    return readme


def _commands(parsed, phase: CommandPhase) -> list[str]:
    return [gc.raw for gc in parsed.get_commands(phase)]


@pytest.mark.parametrize(
    "heading",
    [KEDA_HEADING, "Configure Autoscaling", "Apply the KEDA ScaledObject"],
)
def test_autoscaling_section_gets_its_own_phase(tmp_path: Path, heading: str):
    readme = _write_guide(tmp_path, README.replace(KEDA_HEADING, heading))

    parsed = parse_guide_readme(readme)

    autoscaling = _commands(parsed, CommandPhase.AUTOSCALING)
    assert [c.split()[1] for c in autoscaling] == ["apply", "wait"]
    assert "keda/ocp" in autoscaling[0]
    assert not any("keda" in c for c in _commands(parsed, CommandPhase.MODELSERVER))
    # The next heading leaves the autoscaling phase.
    assert _commands(parsed, CommandPhase.VERIFY) == [
        "kubectl get pods -n ${NAMESPACE}"
    ]


def test_keda_title_does_not_route_preamble_commands(tmp_path: Path):
    readme = _write_guide(
        tmp_path,
        """\
        # Guide + KEDA Autoscaling

        ```bash
        kubectl get nodes
        ```

        ## Prerequisites
        """,
    )

    assert parse_guide_readme(readme).commands == []


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
    _write_guide(repo)
    stack = tmp_path / "stack"
    stack.mkdir()
    config = {"enabled": True, "guideName": GUIDE, "repoPath": str(repo)}
    (stack / "config.yaml").write_text(
        yaml.safe_dump({"kustomize": {**config, "deployTimeout": 10}}),
        encoding="utf-8",
    )
    for helper in ("_ensure_hf_token_secret", "_propagate_standup_parameters"):
        monkeypatch.setattr(KustomizeDeployStep, helper, lambda *a, **k: None)

    def _deploy(cmd: _RecordingCmd):
        ctx = MagicMock(harness_namespace=None, deployed_endpoints={})
        ctx.require_cmd.return_value = cmd
        ctx.require_namespace.return_value = "ns"
        return KustomizeDeployStep().execute(ctx, stack)

    return _deploy


def test_autoscaling_runs_after_the_pod_wait(deploy):
    cmd = _RecordingCmd()

    result = deploy(cmd)

    assert result.success, result.errors
    order = [
        next(i for i, e in enumerate(cmd.events) if marker in e)
        for marker in ("wait_for_pods", "keda/ocp", "scaledobject/")
    ]
    assert order == sorted(order)


def test_autoscaling_failure_fails_the_step(deploy):
    result = deploy(_RecordingCmd(fail_on="keda/ocp"))

    assert not result.success
    assert result.message == "Autoscaling deployment failed"
    assert "boom" in result.errors[0]
