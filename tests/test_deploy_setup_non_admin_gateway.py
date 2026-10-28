"""Step 06 must not install the gateway provider under ``--non-admin``.

``09_helmfile-gateway-provider.yaml`` installs a cluster-scoped control
plane (istio-base + istiod, or agentgateway-crds + agentgateway) in a
shared namespace such as ``istio-system``. Step 02 skips its own provider
install under ``--non-admin``; step 06 used to apply the same helmfile
regardless, so a namespace-admin run could upgrade the gateway every other
tenant on the cluster uses, or fail for lack of rights in that namespace.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.standup.steps.step_06_deploy_setup import DeploySetupStep

_PROVIDER_HELMFILE = "09_helmfile-gateway-provider.yaml"

_ISTIO_PROVIDER = """\
releases:
  - name: istio-base
    chart: istio/base
    namespace: istio-system
  - name: istiod
    chart: istio/istiod
    namespace: istio-system
"""


def _stack(tmp_path: Path, gateway_class: str = "istio") -> Path:
    stack_path = tmp_path / "stack"
    stack_path.mkdir()
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(
            {"release": "llmdbench", "gateway": {"className": gateway_class}}
        ),
        encoding="utf-8",
    )
    (stack_path / _PROVIDER_HELMFILE).write_text(_ISTIO_PROVIDER, encoding="utf-8")
    return stack_path


def _context(tmp_path: Path, non_admin: bool) -> SimpleNamespace:
    cmd = MagicMock()
    cmd.helmfile.return_value = SimpleNamespace(success=True, stderr="")
    return SimpleNamespace(
        non_admin=non_admin,
        is_openshift=False,
        logger=MagicMock(),
        require_cmd=lambda: cmd,
        require_namespace=lambda: "llmdbench",
        setup_helm_dir=lambda: tmp_path / "helm",
    )


def _provider_applies(context: SimpleNamespace) -> list:
    cmd = context.require_cmd()
    return [
        call
        for call in cmd.helmfile.call_args_list
        if any(str(arg).endswith(_PROVIDER_HELMFILE) for arg in call.args)
    ]


def _info_text(context: SimpleNamespace) -> str:
    return "\n".join(
        str(call.args[0]) for call in context.logger.log_info.call_args_list
    )


@pytest.mark.parametrize("gateway_class", ["istio", "agentgateway"])
def test_non_admin_skips_gateway_provider(tmp_path, gateway_class) -> None:
    context = _context(tmp_path, non_admin=True)

    result = DeploySetupStep().execute(context, _stack(tmp_path, gateway_class))

    assert result.success, result.errors
    assert _provider_applies(context) == [], (
        "--non-admin must not install or upgrade the cluster-scoped gateway provider"
    )
    info = _info_text(context)
    assert "Non-admin: skipping gateway provider install" in info
    assert f"gateway.className={gateway_class}" in info


def test_admin_applies_gateway_provider(tmp_path) -> None:
    context = _context(tmp_path, non_admin=False)

    result = DeploySetupStep().execute(context, _stack(tmp_path))

    assert result.success, result.errors
    applies = _provider_applies(context)
    assert len(applies) == 1
    assert applies[0].args[0] == "apply"
    assert applies[0].kwargs == {"use_kubeconfig": False}
