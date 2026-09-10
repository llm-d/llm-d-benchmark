"""Tests for the keda-prometheus-auth ServiceAccount pre-creation in step_02.

This closes an ordering gap: stacks using ``keda.prometheus.authMode:
bearer-secret`` mint a bearer token from a ServiceAccount in step_03
(workload_monitoring), but cluster-config overlays only create that
ServiceAccount via Helm ``extraObjects`` in step_09 (deploy_modelservice) --
six steps later. See llmdbenchmark/standup/steps/step_02_admin_prerequisites.py
(_ensure_keda_prometheus_service_accounts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import yaml

from llmdbenchmark.standup.steps.step_02_admin_prerequisites import (
    AdminPrerequisitesStep,
)


@dataclass
class _Result:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _Cmd:
    calls: list[tuple[str, ...]] = field(default_factory=list)
    logger: MagicMock = field(default_factory=MagicMock)

    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(tuple(args))
        if args[:2] == ("create", "serviceaccount"):
            sa_name = args[2]
            namespace = args[args.index("-n") + 1]
            return _Result(
                stdout=(
                    "apiVersion: v1\nkind: ServiceAccount\nmetadata:\n"
                    f"  name: {sa_name}\n  namespace: {namespace}\n"
                )
            )
        return _Result(success=True)


@dataclass
class _FailApplyCmd(_Cmd):
    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(tuple(args))
        if args[:2] == ("create", "serviceaccount"):
            return super().kube(*args)
        if args[0] == "apply":
            return _Result(success=False, stderr="forbidden")
        return _Result(success=True)


def _context(rendered_stacks: list[Path], dry_run: bool = False) -> MagicMock:
    context = MagicMock()
    context.rendered_stacks = rendered_stacks
    context.dry_run = dry_run
    return context


def _write_stack(tmp_path: Path, name: str, cfg: dict) -> Path:
    stack_dir = tmp_path / name
    stack_dir.mkdir(parents=True)
    (stack_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    return stack_dir


def _bearer_secret_cfg(namespace: str, sa_name: str | None = None) -> dict:
    prometheus_cfg: dict[str, Any] = {"authMode": "bearer-secret"}
    if sa_name:
        prometheus_cfg["saName"] = sa_name
    return {
        "namespace": {"name": namespace},
        "keda": {
            "scaledObjects": [{"name": "so1"}],
            "prometheus": prometheus_cfg,
        },
    }


class TestEnsureKedaPrometheusServiceAccounts:
    def test_creates_sa_for_bearer_secret_stack(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", _bearer_secret_cfg("ns1"))
        step = AdminPrerequisitesStep()
        cmd = _Cmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(cmd, _context([stack]), errors)

        assert errors == []
        create_calls = [c for c in cmd.calls if c[:2] == ("create", "serviceaccount")]
        assert create_calls and create_calls[0][2] == "keda-prometheus-auth"
        applied = [c for c in cmd.calls if c[0] == "apply"]
        assert len(applied) == 1

    def test_respects_custom_sa_name(self, tmp_path: Path) -> None:
        stack = _write_stack(
            tmp_path, "s1", _bearer_secret_cfg("ns1", sa_name="my-custom-sa")
        )
        step = AdminPrerequisitesStep()
        cmd = _Cmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(cmd, _context([stack]), errors)

        create_calls = [c for c in cmd.calls if c[:2] == ("create", "serviceaccount")]
        assert create_calls[0][2] == "my-custom-sa"

    def test_skips_when_auth_mode_is_none(self, tmp_path: Path) -> None:
        cfg = _bearer_secret_cfg("ns1")
        cfg["keda"]["prometheus"]["authMode"] = "none"
        stack = _write_stack(tmp_path, "s1", cfg)
        step = AdminPrerequisitesStep()
        cmd = _Cmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(cmd, _context([stack]), errors)

        assert cmd.calls == []
        assert errors == []

    def test_skips_when_no_scaled_objects(self, tmp_path: Path) -> None:
        stack = _write_stack(
            tmp_path,
            "s1",
            {
                "namespace": {"name": "ns1"},
                "keda": {"prometheus": {"authMode": "bearer-secret"}},
            },
        )
        step = AdminPrerequisitesStep()
        cmd = _Cmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(cmd, _context([stack]), errors)

        assert cmd.calls == []

    def test_dedups_across_stacks_sharing_namespace(self, tmp_path: Path) -> None:
        s1 = _write_stack(tmp_path, "s1", _bearer_secret_cfg("ns1"))
        s2 = _write_stack(tmp_path, "s2", _bearer_secret_cfg("ns1"))
        step = AdminPrerequisitesStep()
        cmd = _Cmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(cmd, _context([s1, s2]), errors)

        create_calls = [c for c in cmd.calls if c[:2] == ("create", "serviceaccount")]
        assert len(create_calls) == 1

    def test_dry_run_is_noop(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", _bearer_secret_cfg("ns1"))
        step = AdminPrerequisitesStep()
        cmd = _Cmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(
            cmd, _context([stack], dry_run=True), errors
        )

        assert cmd.calls == []

    def test_apply_failure_appends_error(self, tmp_path: Path) -> None:
        stack = _write_stack(tmp_path, "s1", _bearer_secret_cfg("ns1"))
        step = AdminPrerequisitesStep()
        cmd = _FailApplyCmd()
        errors: list = []

        step._ensure_keda_prometheus_service_accounts(cmd, _context([stack]), errors)

        assert len(errors) == 1
        assert "forbidden" in errors[0]
