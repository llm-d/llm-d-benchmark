"""Tests for llmdbenchmark/standup/keda.py stack-discovery and install helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.standup.lib.keda import (
    install_keda_for_namespace,
    stacks_enabling_keda,
)
from llmdbenchmark.standup.steps.step_03_workload_monitoring import (
    WorkloadMonitoringStep,
)
from llmdbenchmark.standup.steps.step_09_deploy_modelservice import (
    DeployModelserviceStep,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")

    def log_error(self, msg: str, **_: Any) -> None:
        self.messages.append(f"ERR: {msg}")


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    kube_calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.kube_calls.append(args)
        return _StubResult(success=True)


@dataclass
class _StubContext:
    logger: _StubLogger = field(default_factory=_StubLogger)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_stack(tmp_path: Path, name: str, *, cfg: dict) -> Path:
    stack_dir = tmp_path / name
    stack_dir.mkdir(parents=True)
    (stack_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    return stack_dir


def _write_ta_template(stack_dir: Path, namespace: str, secret_name: str) -> None:
    """Write a rendered TriggerAuthentication YAML (template 32)."""
    content = f"""apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: keda-prometheus-auth
  namespace: {namespace}
spec:
  secretTargetRef:
  - parameter: bearerToken
    name: {secret_name}
    key: bearerToken
  - parameter: ca
    name: {secret_name}
    key: ca.crt
"""
    (stack_dir / "27a_keda-triggerauthentication.yaml").write_text(content)


def _write_so_template(stack_dir: Path) -> None:
    """Write a minimal rendered ScaledObjects YAML (template 31)."""
    content = """apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: test-so
  namespace: test-ns
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: test-deploy
  minReplicaCount: 1
  maxReplicaCount: 5
  triggers: []
"""
    (stack_dir / "27_keda-scaledobjects.yaml").write_text(content)


# ---------------------------------------------------------------------------
# Tests: stacks_enabling_keda
# ---------------------------------------------------------------------------


class TestStacksEnablingKeda:
    def test_returns_stack_with_scaled_objects(self, tmp_path: Path) -> None:
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {"scaledObjects": [{"name": "so1"}]},
            },
        )
        result = stacks_enabling_keda([stack])
        assert len(result) == 1
        assert result[0][0] == stack

    def test_empty_scaled_objects_excluded(self, tmp_path: Path) -> None:
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {"scaledObjects": []},
            },
        )
        assert stacks_enabling_keda([stack]) == []

    def test_missing_keda_key_excluded(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", cfg={"namespace": {"name": "ns1"}})
        assert stacks_enabling_keda([stack]) == []

    def test_missing_config_yaml_excluded(self, tmp_path: Path) -> None:
        stack_dir = tmp_path / "empty"
        stack_dir.mkdir()
        assert stacks_enabling_keda([stack_dir]) == []

    def test_multiple_stacks_only_enabled_returned(self, tmp_path: Path) -> None:
        s1 = _write_stack(
            tmp_path, "s1", cfg={"keda": {"scaledObjects": [{"name": "x"}]}}
        )
        s2 = _write_stack(tmp_path, "s2", cfg={"namespace": {"name": "ns2"}})
        result = stacks_enabling_keda([s1, s2])
        assert len(result) == 1
        assert result[0][0] == s1


# ---------------------------------------------------------------------------
# Tests: install_keda_for_namespace
# ---------------------------------------------------------------------------


class TestInstallKedaForNamespace:
    def test_none_auth_applies_scaledobjects_only(self, tmp_path: Path) -> None:
        """authMode=none: applies ScaledObjects template, no TriggerAuthentication."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {"authMode": "none"},
                    "scaledObjects": [{"name": "x"}],
                },
            },
        )
        _write_so_template(stack)
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert errors == []
        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        # Only the ScaledObjects template applied — no TA
        assert len(applied) == 1
        assert "27_keda-scaledobjects" in applied[0][2]

    def test_bearer_secret_auth_applies_ta_then_scaledobjects(
        self, tmp_path: Path
    ) -> None:
        """authMode=bearer-secret: applies TA first, then ScaledObjects."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {
                        "authMode": "bearer-secret",
                        "secretName": "my-secret",
                    },
                    "scaledObjects": [{"name": "x"}],
                },
            },
        )
        _write_ta_template(stack, "test-ns", "my-secret")
        _write_so_template(stack)
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert errors == []
        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        assert len(applied) == 2
        paths_applied = [args[2] for args in applied]
        assert any("27a_keda-triggerauthentication" in p for p in paths_applied)
        assert any("27_keda-scaledobjects" in p for p in paths_applied)
        # TA must be applied before ScaledObjects
        ta_idx = next(i for i, p in enumerate(paths_applied) if "27a_keda" in p)
        so_idx = next(
            i for i, p in enumerate(paths_applied) if "27_keda-scaledobjects" in p
        )
        assert ta_idx < so_idx

    def test_bearer_secret_missing_ta_template_warns(self, tmp_path: Path) -> None:
        """Missing TA template logs a warning but does not append an error."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {
                        "authMode": "bearer-secret",
                        "secretName": "my-secret",
                    },
                    "scaledObjects": [{"name": "x"}],
                },
            },
        )
        _write_so_template(stack)  # no TA template
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert any("WARN" in m for m in ctx.logger.messages)

    def test_missing_so_template_is_noop(self, tmp_path: Path) -> None:
        """Missing ScaledObjects template: nothing applied, no errors."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {"authMode": "none"},
                    "scaledObjects": [{"name": "x"}],
                },
            },
        )
        # No template file written
        cmd = _StubCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert errors == []
        assert cmd.kube_calls == []

    def test_kube_apply_failure_appends_error(self, tmp_path: Path) -> None:
        """A kubectl apply failure appends to errors list."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {"authMode": "none"},
                    "scaledObjects": [{"name": "x"}],
                },
            },
        )
        _write_so_template(stack)

        @dataclass
        class _FailCmd:
            kube_calls: list = field(default_factory=list)

            def kube(self, *args: str, **_: Any) -> _StubResult:
                self.kube_calls.append(args)
                return _StubResult(success=False, stderr="permission denied")

        cmd = _FailCmd()
        ctx = _StubContext()
        errors: list = []

        install_keda_for_namespace(cmd, ctx, stack, "test-ns", errors)

        assert len(errors) == 1
        assert "permission denied" in errors[0]


# ---------------------------------------------------------------------------
# Tests: step_03 integration — _install_keda_if_enabled
# ---------------------------------------------------------------------------


@dataclass
class _FullStubContext:
    rendered_stacks: list[Path] = field(default_factory=list)
    is_openshift: bool = False  # deliberately False to test non-OCP path
    platform_type: str = "kind"
    logger: _StubLogger = field(default_factory=_StubLogger)
    dry_run: bool = False
    non_admin: bool = False


class TestInstallKedaIfEnabled:
    def test_runs_on_non_openshift(self, tmp_path: Path) -> None:
        """_install_keda_if_enabled runs even when is_openshift is False."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {"authMode": "none"},
                    "scaledObjects": [{"name": "x"}],
                },
                "namespace": {"name": "ns1"},
            },
        )
        _write_so_template(stack)
        step = WorkloadMonitoringStep()
        ctx = _FullStubContext(rendered_stacks=[stack], is_openshift=False)
        cmd = _StubCmd()
        errors: list = []

        step._install_keda_if_enabled(cmd, ctx, errors)

        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        assert len(applied) == 1, (
            f"Expected ScaledObjects apply on non-OCP; kube_calls={cmd.kube_calls}"
        )

    def test_no_keda_stacks_is_noop(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", cfg={"namespace": {"name": "ns1"}})
        step = WorkloadMonitoringStep()
        ctx = _FullStubContext(rendered_stacks=[stack])
        cmd = _StubCmd()
        errors: list = []

        step._install_keda_if_enabled(cmd, ctx, errors)

        assert cmd.kube_calls == []
        assert errors == []

    def test_keda_crd_check_issued_when_stacks_present(self, tmp_path: Path) -> None:
        """verify_keda_installed is called when keda-enabled stacks are found."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {"authMode": "none"},
                    "scaledObjects": [{"name": "x"}],
                },
                "namespace": {"name": "ns1"},
            },
        )
        _write_so_template(stack)
        step = WorkloadMonitoringStep()
        ctx = _FullStubContext(rendered_stacks=[stack])
        cmd = _StubCmd()

        step._install_keda_if_enabled(cmd, ctx, [])

        crd_checks = [
            args for args in cmd.kube_calls if "scaledobjects.keda.sh" in args
        ]
        assert len(crd_checks) == 1, (
            f"Expected KEDA CRD check; kube_calls={cmd.kube_calls}"
        )

    def test_keda_crd_missing_logs_warning(self, tmp_path: Path) -> None:
        """A missing KEDA CRD logs a warning but does not abort or append an error."""
        stack = _write_stack(
            tmp_path,
            "s1",
            cfg={
                "keda": {
                    "prometheus": {"authMode": "none"},
                    "scaledObjects": [{"name": "x"}],
                },
                "namespace": {"name": "ns1"},
            },
        )
        _write_so_template(stack)

        @dataclass
        class _CrdMissingCmd:
            kube_calls: list = field(default_factory=list)

            def kube(self, *args: str, **_: Any) -> _StubResult:
                self.kube_calls.append(args)
                if "scaledobjects.keda.sh" in args:
                    return _StubResult(success=False, stderr="not found")
                return _StubResult(success=True)

        step = WorkloadMonitoringStep()
        ctx = _FullStubContext(rendered_stacks=[stack])
        cmd = _CrdMissingCmd()
        errors: list = []

        step._install_keda_if_enabled(cmd, ctx, errors)

        assert any("WARN" in m for m in ctx.logger.messages), (
            f"Expected KEDA-not-installed warning; messages={ctx.logger.messages}"
        )
        assert errors == [], "Missing KEDA CRD should warn, not error"


# ---------------------------------------------------------------------------
# Tests: step_09 integration — _apply_keda_stack_resources
# ---------------------------------------------------------------------------


class TestApplyKedaStackResources:
    def test_applies_scaledobjects_template(self, tmp_path: Path) -> None:
        stack = tmp_path / "s1"
        stack.mkdir()
        _write_so_template(stack)
        step = DeployModelserviceStep()
        cmd = _StubCmd()
        errors: list = []

        step._apply_keda_stack_resources(cmd, stack, errors)

        applied = [args for args in cmd.kube_calls if args[0] == "apply"]
        assert len(applied) == 1
        assert "27_keda-scaledobjects" in applied[0][2]
        assert errors == []

    def test_missing_template_is_noop(self, tmp_path: Path) -> None:
        stack = tmp_path / "s1"
        stack.mkdir()
        step = DeployModelserviceStep()
        cmd = _StubCmd()
        errors: list = []

        step._apply_keda_stack_resources(cmd, stack, errors)

        assert cmd.kube_calls == []
        assert errors == []

    def test_apply_failure_appends_error(self, tmp_path: Path) -> None:
        stack = tmp_path / "s1"
        stack.mkdir()
        _write_so_template(stack)

        @dataclass
        class _FailCmd:
            kube_calls: list = field(default_factory=list)

            def kube(self, *args: str, **_: Any) -> _StubResult:
                self.kube_calls.append(args)
                return _StubResult(success=False, stderr="forbidden")

        step = DeployModelserviceStep()
        errors: list = []

        step._apply_keda_stack_resources(_FailCmd(), stack, errors)

        assert len(errors) == 1
        assert "forbidden" in errors[0]
