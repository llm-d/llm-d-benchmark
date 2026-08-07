"""Tests for llmdbenchmark/standup/keda.py stack-discovery and install helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.standup.keda import stacks_enabling_keda, install_keda_for_namespace


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
    (stack_dir / "32_keda-triggerauthentication.yaml").write_text(content)


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
    (stack_dir / "31_keda-scaledobjects.yaml").write_text(content)


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
        assert "31_keda-scaledobjects" in applied[0][2]

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
        assert any("32_keda-triggerauthentication" in p for p in paths_applied)
        assert any("31_keda-scaledobjects" in p for p in paths_applied)
        # TA must be applied before ScaledObjects
        ta_idx = next(i for i, p in enumerate(paths_applied) if "32_keda" in p)
        so_idx = next(i for i, p in enumerate(paths_applied) if "31_keda" in p)
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
