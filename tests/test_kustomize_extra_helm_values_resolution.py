"""``kustomize.extraHelmValues`` / ``extraHelmSets`` get the README command's resolution.

The extra args are appended to a router command that has already been resolved
(``${VAR}`` placeholders, relative ``guides/...`` paths), but they were appended
raw: ``${REPO_ROOT}/guides/<guide>/router/x.values.yaml`` became
``<cwd>/${REPO_ROOT}/guides/...``, so a scenario could not point at a values file
the guide ships (for example a guide's PD-only router values).
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

```bash
export PROVIDER_NAME=istio
```

### 1. Deploy the llm-d Router

```bash
helm install ${GUIDE_NAME} oci://charts/router -n ${NAMESPACE}
```

### 2. Deploy the Model Server

```bash
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/test-guide/modelserver/
```
"""


class _RecordingCmd:
    """Records helm calls; kube calls and the pod wait succeed."""

    dry_run = False

    def __init__(self):
        self.helm_calls: list[list[str]] = []

    def helm(self, *args, **_kw):
        self.helm_calls.append(list(args))
        return SimpleNamespace(success=True, stdout="", stderr="")

    def kube(self, *_args, **_kw):
        return SimpleNamespace(success=True, stdout="", stderr="")

    def wait_for_pods(self, **_kw):
        return SimpleNamespace(success=True, stdout="", stderr="")


@pytest.fixture
def router_helm_args(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    guide_dir = repo / "guides" / GUIDE
    guide_dir.mkdir(parents=True)
    (guide_dir / "README.md").write_text(README, encoding="utf-8")
    stack = tmp_path / "stack"
    stack.mkdir()
    for helper in ("_ensure_hf_token_secret", "_propagate_standup_parameters"):
        monkeypatch.setattr(KustomizeDeployStep, helper, lambda *a, **k: None)

    def _run(**kustomize) -> list[str]:
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
        cmd = _RecordingCmd()
        ctx = MagicMock(harness_namespace=None, deployed_endpoints={})
        ctx.require_cmd.return_value = cmd
        ctx.require_namespace.return_value = "ns"
        result = KustomizeDeployStep().execute(ctx, stack)
        assert result.success, result.errors
        (install,) = cmd.helm_calls
        return install

    return repo, _run


def test_extra_helm_values_resolve_repo_root(router_helm_args):
    repo, run = router_helm_args

    args = run(
        extraHelmValues=["${REPO_ROOT}/guides/test-guide/router/extra.values.yaml"]
    )

    assert args[-2:] == ["-f", f"{repo}/guides/test-guide/router/extra.values.yaml"]


def test_extra_helm_values_resolve_relative_guide_paths(router_helm_args):
    repo, run = router_helm_args

    args = run(extraHelmValues=["guides/test-guide/router/extra.values.yaml"])

    assert args[-2:] == ["-f", f"{repo}/guides/test-guide/router/extra.values.yaml"]


def test_extra_helm_sets_resolve_readme_variables(router_helm_args):
    _repo, run = router_helm_args

    args = run(extraHelmSets={"provider.name": "${PROVIDER_NAME}"})

    assert args[-2:] == ["--set", "provider.name=istio"]


def test_absolute_extra_helm_values_are_unchanged(router_helm_args):
    _repo, run = router_helm_args

    args = run(extraHelmValues=["/abs/extra.values.yaml"])

    assert args[-2:] == ["-f", "/abs/extra.values.yaml"]
