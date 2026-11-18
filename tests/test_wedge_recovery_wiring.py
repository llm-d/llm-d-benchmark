"""The wedge recovery must reach the helmfile call it protects: the diff
env only on a real clear, and the removed record back only when the
apply that follows fails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.standup.steps.step_06_deploy_setup import DeploySetupStep


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


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    apply_fails: bool = False
    helmfile_calls: list[dict[str, Any]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    def helm(self, *args: str, **_: Any) -> _StubResult:
        if args[0] == "status":
            # a failed first install of the infra release
            return _StubResult(stdout='{"info": {"status": "failed"}, "version": 1}')
        return _StubResult()

    def helmfile(self, *args: str, **kw: Any) -> _StubResult:
        self.helmfile_calls.append({"args": args, **kw})
        if self.apply_fails:
            return _StubResult(success=False, stderr="chart pull failed")
        return _StubResult()

    def kube(self, *args: str, **kw: Any) -> _StubResult:
        if args[0] == "get" and args[1] == "secret":
            return _StubResult(stdout="apiVersion: v1\nkind: Secret\n")
        if args[0] == "create":
            self.created.append(kw.get("namespace"))
            return _StubResult()
        return _StubResult()


@dataclass
class _StubContext:
    rendered_stacks: list[Path]
    workspace: Path
    cmd: _StubCmd
    non_admin: bool = False
    dry_run: bool = False
    is_openshift: bool = False
    kustomize_skip_infra: bool = False
    deployed_methods: list[str] = field(default_factory=lambda: ["modelservice"])
    namespace: str | None = "nikita-dev"
    harness_namespace: str | None = None
    logger: _StubLogger = field(default_factory=_StubLogger)

    def require_cmd(self) -> _StubCmd:
        return self.cmd

    def require_namespace(self) -> str:
        return "nikita-dev"

    def setup_helm_dir(self) -> Path:
        helm = self.workspace / "setup" / "helm"
        helm.mkdir(parents=True, exist_ok=True)
        return helm


def _stack(base: Path) -> Path:
    stack = base / "s1"
    stack.mkdir(parents=True)
    (stack / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "namespace": {"name": "nikita-dev"},
                "release": "llmdbench",
                "gateway": {"namespace": "nikita-dev", "className": "epponly"},
                "helmRepositories": {},
                "model_id_label": "qwen-qwe-6ed7b204-wen3-32b",
            }
        ),
        encoding="utf-8",
    )
    (stack / "10_helmfile-main.yaml").write_text(
        "releases:\n  - name: infra-llmdbench\n    chart: llm-d-infra/llm-d-infra\n",
        encoding="utf-8",
    )
    return stack


def _run(tmp_path: Path, apply_fails: bool):
    cmd = _StubCmd(apply_fails=apply_fails)
    stack = _stack(tmp_path / ("fail" if apply_fails else "ok"))
    context = _StubContext(rendered_stacks=[stack], workspace=tmp_path, cmd=cmd)
    result = DeploySetupStep().execute(context, stack)
    return cmd, result, context.logger


def test_recovery_reaches_the_helmfile_call(tmp_path):
    ok_cmd, ok_result, _logger = _run(tmp_path, apply_fails=False)
    fail_cmd, fail_result, fail_logger = _run(tmp_path, apply_fails=True)

    assert ok_result.success
    assert not fail_result.success
    for cmd in (ok_cmd, fail_cmd):
        assert cmd.helmfile_calls
        assert all(
            call.get("env", {}).get("HELM_DIFF_THREE_WAY_MERGE") == "true"
            for call in cmd.helmfile_calls
        )
    # a successful apply wrote a new record; nothing to put back
    assert ok_cmd.created == []
    assert fail_cmd.created == ["nikita-dev"]
    assert any("restored" in w for w in fail_logger.warnings)
