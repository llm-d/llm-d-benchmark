"""Generic ``keda.scaledObjects`` on a kustomize stack (step_05_kustomize_deploy).

Generic ScaledObjects were applied only in standup step 3, which kustomize-only
stacks skip unless ``--full-infra``; and step 5 deletes every ScaledObject in the
namespace before deploying (``_remove_leftover_autoscalers``), so even with
``--full-infra`` they never survived a kustomize standup. Step 5 now applies them
itself, after the guide is deployed and its autoscaling phase has run.
"""

from __future__ import annotations

import sys
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

from llmdbenchmark.standup.steps.step_05_kustomize_deploy import KustomizeDeployStep

GUIDE = "test-guide"

README = """\
# Test Guide

### 2. Deploy the Model Server

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/modelserver/
```

### 4. Enable Autoscaling (KEDA)

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/keda
```
"""

SCALED_OBJECTS = """\
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: decode
"""

KEDA = {
    "prometheus": {"authMode": "none"},
    "scaledObjects": [{"name": "decode"}],
}


class _RecordingCmd:
    """Records kube calls and the pod wait; lists one leftover ScaledObject."""

    dry_run = False

    def __init__(self, fail_on: str = ""):
        self.events: list[str] = []
        self.fail_on = fail_on

    def kube(self, *args, **_kw):
        line = " ".join(args)
        self.events.append(line)
        stdout = (
            "scaledobject.keda.sh/leftover\n"
            if line.startswith("get scaledobject")
            else ""
        )
        failed = bool(self.fail_on) and self.fail_on in line
        return SimpleNamespace(success=not failed, stdout=stdout, stderr="boom")

    def wait_for_pods(self, **_kw):
        self.events.append("wait_for_pods")
        return SimpleNamespace(success=True, stdout="", stderr="")


@pytest.fixture
def deploy(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    guide_dir = repo / "guides" / GUIDE
    guide_dir.mkdir(parents=True)
    (guide_dir / "README.md").write_text(README, encoding="utf-8")
    stack = tmp_path / "stack"
    stack.mkdir()
    for helper in ("_ensure_hf_token_secret", "_propagate_standup_parameters"):
        monkeypatch.setattr(KustomizeDeployStep, helper, lambda *a, **k: None)

    def _deploy(cmd: _RecordingCmd, keda: dict | None = None):
        config = {
            "kustomize": {
                "enabled": True,
                "guideName": GUIDE,
                "repoPath": str(repo),
                "deployTimeout": 10,
            },
            "namespace": {"name": "ns"},
        }
        if keda is not None:
            config["keda"] = keda
        # Present either way, so only the keda block decides whether it is applied.
        (stack / "27_keda-scaledobjects.yaml").write_text(
            SCALED_OBJECTS, encoding="utf-8"
        )
        (stack / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        ctx = MagicMock(harness_namespace=None, deployed_endpoints={})
        ctx.require_cmd.return_value = cmd
        ctx.require_namespace.return_value = "ns"
        return KustomizeDeployStep().execute(ctx, stack)

    return _deploy


def _index(events: list[str], marker: str) -> int:
    return next(i for i, e in enumerate(events) if marker in e)


def test_generic_scaled_objects_are_applied_after_the_guide_is_deployed(deploy):
    cmd = _RecordingCmd()

    result = deploy(cmd, KEDA)

    assert result.success, result.errors
    order = [
        _index(cmd.events, m)
        for m in (
            "delete scaledobject.keda.sh/leftover",
            "wait_for_pods",
            "test-guide/keda",
            "27_keda-scaledobjects.yaml",
        )
    ]
    assert order == sorted(order)


def test_no_generic_scaled_objects_without_a_keda_block(deploy):
    cmd = _RecordingCmd()

    result = deploy(cmd)

    assert result.success, result.errors
    assert not any("27_keda-scaledobjects" in e for e in cmd.events)


def test_generic_scaled_objects_apply_failure_fails_the_step(deploy):
    result = deploy(_RecordingCmd(fail_on="27_keda-scaledobjects"), KEDA)

    assert not result.success
    assert result.message == "Generic KEDA autoscaling setup failed"
    assert "boom" in result.errors[0]
