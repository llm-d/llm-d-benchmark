"""Teardown with the kustomize method keeps the preprocesses ConfigMap.

Standup creates ``llm-d-benchmark-preprocesses`` for the model and harness
namespaces; a kustomize-deployed guide keeps using it, so teardown must not
remove it while ``kustomize`` is among the deployed methods.

Behavior under test:
- Normal teardown (step 03) skips the ConfigMap with kustomize deployed,
  alone or with another method, and still deletes it otherwise.
- Harness cleanup (step 02) skips it with kustomize deployed.
- The kustomize fallback teardown (step 05) excludes it from the ConfigMap
  delete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.teardown.steps.step_02_clean_harness import CleanHarnessStep
from llmdbenchmark.teardown.steps.step_03_delete_resources import DeleteResourcesStep
from llmdbenchmark.teardown.steps.step_05_kustomize_teardown import (
    KustomizeTeardownStep,
)

PREPROCESSES = "llm-d-benchmark-preprocesses"


class _StubLogger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, *_: Any, **__: Any) -> None:
        pass


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    """Lists *resources* for the namespace query and records every call."""

    resources: list[str] = field(default_factory=list)
    calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.calls.append(args)
        if args[0] == "get" and "-o" in args and args[args.index("-o") + 1] == "name":
            if "-l" in args or "--no-headers" in args:
                return _StubResult()  # prism-protected lookup / prune probe
            return _StubResult(stdout="\n".join(self.resources))
        return _StubResult()

    def helm(self, *args: str, **_: Any) -> _StubResult:
        return _StubResult()

    @property
    def deleted(self) -> list[str]:
        return [c[-1] for c in self.calls if c[0] == "delete"]


@dataclass
class _StubContext:
    rendered_stacks: list[Path]
    deployed_methods: list[str]
    is_openshift: bool = False
    deep_clean: bool = False
    remove_prism: bool = False
    harness_namespace: str = "bench"
    logger: _StubLogger = field(default_factory=_StubLogger)


def _stack(tmp_path: Path) -> Path:
    stack = tmp_path / "s1"
    stack.mkdir()
    cfg = {
        "huggingface": {"secretName": "llm-d-hf-token"},
        "control": {"contextSecretName": "llm-d-benchmark-context"},
        "harness": {"namespace": "bench"},
    }
    (stack / "config.yaml").write_text(yaml.safe_dump(cfg))
    return stack


RESOURCES = [f"configmap/{PREPROCESSES}", "configmap/other", "deployment/vllm"]


@pytest.mark.parametrize("methods", [["kustomize"], ["kustomize", "modelservice"]])
def test_normal_teardown_keeps_configmap_with_kustomize(tmp_path, methods):
    cmd = _StubCmd(resources=RESOURCES)
    context = _StubContext([_stack(tmp_path)], methods)
    DeleteResourcesStep()._normal_clean(cmd, context, ["bench"], [])
    assert f"configmap/{PREPROCESSES}" not in cmd.deleted


def test_normal_teardown_deletes_configmap_without_kustomize(tmp_path):
    cmd = _StubCmd(resources=RESOURCES)
    context = _StubContext([_stack(tmp_path)], ["modelservice"])
    DeleteResourcesStep()._normal_clean(cmd, context, ["bench"], [])
    assert f"configmap/{PREPROCESSES}" in cmd.deleted


@pytest.mark.parametrize(
    "methods,kept", [(["kustomize"], True), (["modelservice"], False)]
)
def test_harness_cleanup(tmp_path, methods, kept):
    cmd = _StubCmd()
    context = _StubContext([_stack(tmp_path)], methods)
    step = CleanHarnessStep()
    step._delete_profile_configmaps = lambda *_: None
    context.require_cmd = lambda: cmd
    step._load_plan_config = lambda _ctx: yaml.safe_load(
        (context.rendered_stacks[0] / "config.yaml").read_text()
    )
    step.execute(context)
    deleted = any(c[0] == "delete" and PREPROCESSES in c for c in cmd.calls)
    assert deleted is not kept


def test_fallback_teardown_excludes_configmap():
    cmd = _StubCmd()
    context = _StubContext([], ["kustomize"])
    KustomizeTeardownStep()._fallback_teardown(cmd, context, "bench", "")
    cm_deletes = [c for c in cmd.calls if c[:2] == ("delete", "configmap")]
    assert cm_deletes
    for call in cm_deletes:
        assert "--all" not in call
        assert f"metadata.name!={PREPROCESSES}" in call
    assert not any("configmap" in c[1] for c in cmd.calls if c[1] != "configmap")
