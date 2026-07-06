"""Tests for Gateway API CRD handling in admin prerequisites."""

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
    "step_02_admin_prerequisites_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_02_admin_prerequisites_isolated"] = _module
_spec.loader.exec_module(_module)
AdminPrerequisitesStep = _module.AdminPrerequisitesStep


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
        if args[:3] == ("apply", "--server-side", "-k"):
            return _Result(
                success=False,
                stderr=(
                    'Apply failed with 1 conflict: conflict with "kube-addon-manager": '
                    ".metadata.annotations.gateway.networking.k8s.io/bundle-version"
                ),
            )
        return _Result(success=True)

    def helm(self, *args: str, **_: Any) -> _Result:
        self.calls.append(("helm", *args))
        return _Result(success=True)


def _plan_config() -> dict[str, Any]:
    return {
        "gatewayApiCrd": {
            "revision": "v1.5.1",
            "crdUrlTemplate": (
                "github.com/kubernetes-sigs/gateway-api/config/crd?ref={revision}"
            ),
        },
        "helmRepositories": {},
        "monitoring": {},
    }


def _context(methods: list[str], cmd: _Cmd) -> MagicMock:
    context = MagicMock()
    context.deployed_methods = methods
    context.dry_run = False
    context.non_admin = False
    context.kustomize_skip_infra = False
    context.require_cmd.return_value = cmd
    context.logger = MagicMock()
    return context


def test_standalone_only_does_not_install_gateway_api_crds() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()
    step._load_plan_config = MagicMock(return_value=_plan_config())
    step._get_existing_crds = MagicMock(
        return_value=["gatewayclasses.gateway.networking.k8s.io"]
    )
    step._apply_namespace_yaml = MagicMock()
    step._apply_openshift_sccs = MagicMock()

    result = step.execute(_context(["standalone"], cmd))

    assert result.success
    assert ("apply", "--server-side", "-k") not in [call[:3] for call in cmd.calls]


def test_modelservice_installs_missing_gateway_api_crds() -> None:
    cmd = _Cmd()
    step = AdminPrerequisitesStep()

    errors: list[str] = []

    step._install_gateway_api_crds(
        cmd,
        _plan_config(),
        errors,
        existing_crds=["gatewayclasses.gateway.networking.k8s.io"],
    )

    assert ("apply", "--server-side", "-k") in [call[:3] for call in cmd.calls]
    assert errors
