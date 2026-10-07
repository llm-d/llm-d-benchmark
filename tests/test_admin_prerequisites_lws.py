"""Tests for LeaderWorkerSet installation in admin prerequisites."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "standup"
    / "steps"
    / "step_02_admin_prerequisites.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_02_admin_prerequisites_lws_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_02_admin_prerequisites_lws_isolated"] = _module
_spec.loader.exec_module(_module)
AdminPrerequisitesStep = _module.AdminPrerequisitesStep
LWS_CRDS = _module.LWS_CRDS


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
        self.calls.append(("kubectl", *args))
        return _Result()

    def helm(self, *args: str, **_: Any) -> _Result:
        self.calls.append(("helm", *args))
        return _Result()


def _plan_config(**overrides: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "lws": {
            "enabled": False,
            "namespace": "lws-system",
            "helmRepository": "oci://registry.k8s.io/lws/charts/",
        },
        "multinode": {"enabled": False},
        "chartVersions": {"lws": "v0.11.1"},
        "helmRepositories": {},
        "monitoring": {},
    }
    for key, value in overrides.items():
        config[key] = {**config[key], **value}
    return config


def _lws_installs(cmd: _Cmd) -> list[tuple[str, ...]]:
    return [c for c in cmd.calls if c[:4] == ("helm", "upgrade", "--install", "lws")]


def _context(methods: list[str], cmd: _Cmd) -> MagicMock:
    context = MagicMock()
    context.deployed_methods = methods
    context.dry_run = False
    context.non_admin = False
    context.kustomize_skip_infra = False
    context.require_cmd.return_value = cmd
    context.logger = MagicMock()
    return context


def test_lws_enabled_installs_when_crds_missing() -> None:
    cmd = _Cmd()
    errors: list[str] = []

    AdminPrerequisitesStep()._install_lws_if_needed(
        cmd, _plan_config(lws={"enabled": True}), errors, existing_crds=[]
    )

    (call,) = _lws_installs(cmd)
    assert "v0.11.1" in call
    assert "enableDisaggregatedSet=true" in call
    assert not errors


def test_multinode_enabled_still_installs() -> None:
    cmd = _Cmd()

    AdminPrerequisitesStep()._install_lws_if_needed(
        cmd, _plan_config(multinode={"enabled": True}), [], existing_crds=[]
    )

    assert len(_lws_installs(cmd)) == 1


def test_neither_flag_skips_install() -> None:
    cmd = _Cmd()

    AdminPrerequisitesStep()._install_lws_if_needed(
        cmd, _plan_config(), [], existing_crds=[]
    )

    assert not _lws_installs(cmd)


def test_existing_crds_skip_install() -> None:
    cmd = _Cmd()

    AdminPrerequisitesStep()._install_lws_if_needed(
        cmd, _plan_config(lws={"enabled": True}), [], existing_crds=list(LWS_CRDS)
    )

    assert not _lws_installs(cmd)


def test_kustomize_method_installs_lws() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    step._load_plan_config = MagicMock(return_value=_plan_config(lws={"enabled": True}))
    step._get_existing_crds = MagicMock(return_value=[])
    step._apply_namespace_yaml = MagicMock()
    step._apply_openshift_sccs = MagicMock()

    result = step.execute(_context(["kustomize"], cmd))

    assert result.success
    assert len(_lws_installs(cmd)) == 1
