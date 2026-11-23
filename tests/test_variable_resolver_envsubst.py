"""Guide variables reach `| envsubst` stages of a resolved pipeline.

Guide READMEs build manifests that still carry `${VAR}` placeholders and pipe
them through `envsubst`, which reads the variables from the process
environment:

    kustomize build ${REPO_ROOT}/guides/${GUIDE_NAME}/router/httproute/base/ | envsubst | kubectl apply -n ${NAMESPACE} -f -

`GuideVariableResolver` substitutes `${VAR}` in the command text, but the
shell that runs the pipeline never had `GUIDE_NAME` or `GATEWAY_ADDRESS`
exported, so `envsubst` replaced the manifest's placeholders with empty
strings (an HTTPRoute with an empty name; a Coordinator with
`gateway.address: ""`). These tests run the resolved command through bash and
envsubst with those variables absent from the environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from llmdbenchmark.kustomize.variable_resolver import GuideVariableResolver

_REPO_PATH = str(Path("/tmp/llmd-repo").resolve())
_GUIDE_VARS = (
    "GUIDE_NAME",
    "NAMESPACE",
    "GATEWAY_ADDRESS",
    "PROVIDER_NAME",
    "ROUTER_VALUES",
    "QUOTED",
    "UNRESOLVED",
    "FROM_CLUSTER",
)

needs_envsubst = pytest.mark.skipif(
    shutil.which("envsubst") is None, reason="envsubst (gettext) is required"
)


def _resolver(readme_variables=None):
    return GuideVariableResolver(
        guide_name="coord-disaggregation",
        namespace="bench-ns",
        gaie_version="v1.6.0",
        repo_path=_REPO_PATH,
        readme_variables=readme_variables or {},
    )


def _manifest(tmp_path, text: str) -> str:
    """A manifest on disk, as `kustomize build` would emit it: its placeholders
    are not in the command text, so only envsubst can fill them."""
    f = tmp_path / "manifest.yaml"
    f.write_text(text)
    return str(f)


def _run(cmd: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _GUIDE_VARS}
    result = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout


@needs_envsubst
def test_envsubst_stage_sees_the_guide_variables(tmp_path):
    resolver = _resolver(
        {
            "PROVIDER_NAME": "istio",
            "GATEWAY_ADDRESS": "http://llm-d-inference-gateway-${PROVIDER_NAME}.${NAMESPACE}.svc:80",
        }
    )
    f = _manifest(tmp_path, "name: ${GUIDE_NAME}\naddress: ${GATEWAY_ADDRESS}\n")
    out = _run(resolver.resolve(f"cat {f} | envsubst"))
    assert (
        out
        == "name: coord-disaggregation\naddress: http://llm-d-inference-gateway-istio.bench-ns.svc:80\n"
    )


@needs_envsubst
def test_envsubst_in_the_middle_of_a_pipeline(tmp_path):
    f = _manifest(tmp_path, "ns: ${NAMESPACE}\n")
    out = _run(_resolver().resolve(f"cat {f} | envsubst | cat"))
    assert out == "ns: bench-ns\n"


@needs_envsubst
def test_values_with_spaces_and_quotes_survive(tmp_path):
    resolver = _resolver({"QUOTED": "it's -f a b"})
    f = _manifest(tmp_path, "[${QUOTED}]\n")
    out = _run(resolver.resolve(f"cat {f} | envsubst"))
    assert out == "[it's -f a b]\n"


@needs_envsubst
def test_unresolved_and_command_substitution_values_are_not_exported(tmp_path):
    marker = tmp_path / "ran"
    resolver = _resolver(
        {"UNRESOLVED": "${NOT_DEFINED}", "FROM_CLUSTER": f"$(touch {marker})"}
    )
    f = _manifest(tmp_path, "[${UNRESOLVED}][${FROM_CLUSTER}]\n")
    out = _run(resolver.resolve(f"cat {f} | envsubst"))
    assert out == "[][]\n"  # as before: unset, so envsubst writes empty strings
    assert not marker.exists()  # the value is never executed


def test_commands_without_envsubst_are_unchanged():
    resolver = _resolver()
    cmd = "kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/coordinator/monitoring"
    assert (
        resolver.resolve(cmd)
        == f"kubectl apply -n bench-ns -k {_REPO_PATH}/guides/coord-disaggregation/coordinator/monitoring"
    )
