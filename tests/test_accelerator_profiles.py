"""Tests for auto-detected accelerator runtime profiles."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from llmdbenchmark.parser.cluster_resource_resolver import (
    ClusterResourceResolver,
    NodeResources,
)
from llmdbenchmark.parser.render_plans import RenderPlans
from llmdbenchmark.parser.version_resolver import VersionResolver


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PROJECT_ROOT / "config" / "templates" / "jinja"
DEFAULTS = PROJECT_ROOT / "config" / "templates" / "values" / "defaults.yaml"
GUIDE = PROJECT_ROOT / "config" / "scenarios" / "guides" / "optimized-baseline.yaml"
XPU_GUIDES = (
    "optimized-baseline.yaml",
    "pd-disaggregation.yaml",
    "precise-prefix-cache-routing.yaml",
)


def _render(
    tmp_path: Path,
    profile: str,
    resource: str | None,
    guide: Path = GUIDE,
    setup_overrides: dict | None = None,
) -> tuple[object, dict]:
    logger = MagicMock()
    accelerator_override = {"profile": profile}
    if resource is not None:
        accelerator_override["resource"] = resource
    overrides = {"accelerator": accelerator_override}
    if setup_overrides:
        overrides.update(setup_overrides)
    renderer = RenderPlans(
        template_dir=TEMPLATES,
        defaults_file=DEFAULTS,
        scenarios_file=guide,
        output_dir=tmp_path,
        logger=logger,
        setup_overrides=overrides,
        version_resolver=VersionResolver(logger=logger, dry_run=True),
        cluster_resource_resolver=ClusterResourceResolver(logger=logger, dry_run=True),
    )
    result = renderer.eval()
    assert len(result.rendered_paths) == 1
    config_path = result.rendered_paths[0] / "config.yaml"
    assert config_path.exists()
    return result, yaml.safe_load(config_path.read_text())


def test_intel_xe_resource_resolves_profile_and_type():
    resolver = ClusterResourceResolver(logger=MagicMock(), dry_run=False)
    resolver._node_resources = NodeResources(accelerator_resources=["gpu.intel.com/xe"])
    values = {"accelerator": {"resource": "auto", "profile": "auto"}}
    unresolved: list[str] = []

    resolver._resolve_accelerator_resource(values, unresolved)
    resolver._resolve_accelerator_profile(values, unresolved)

    assert unresolved == []
    assert values["accelerator"] == {
        "resource": "gpu.intel.com/xe",
        "profile": "intel-xe",
        "type": "intel-xe",
    }


def test_intel_i915_and_xe_aliases_prefer_xe():
    resolver = ClusterResourceResolver(logger=MagicMock(), dry_run=False)
    resolver._node_resources = NodeResources(
        accelerator_resources=["gpu.intel.com/i915", "gpu.intel.com/xe"]
    )
    values = {"accelerator": {"resource": "auto", "profile": "auto"}}
    unresolved: list[str] = []

    resolver._resolve_accelerator_resource(values, unresolved)
    resolver._resolve_accelerator_profile(values, unresolved)

    assert unresolved == []
    assert values["accelerator"]["resource"] == "gpu.intel.com/xe"
    assert values["accelerator"]["profile"] == "intel-xe"


def test_intel_i915_uses_the_shared_xpu_profile(tmp_path):
    result, merged = _render(tmp_path, "intel-i915", "gpu.intel.com/i915")

    assert not result.has_errors
    assert merged["accelerator"]["type"] == "intel-i915"
    assert merged["accelerator"]["resource"] == "gpu.intel.com/i915"
    assert "llm-d-xpu" in merged["images"]["vllm"]["repository"]
    assert "--enforce-eager" in merged["decode"]["vllm"]["customCommand"]


def test_explicit_profile_resolves_resource_without_cluster():
    resolver = ClusterResourceResolver(logger=MagicMock(), dry_run=False)

    resolved = resolver.resolve_all(
        {"accelerator": {"profile": "intel-xe", "resource": "auto"}}
    )

    assert resolved["accelerator"]["resource"] == "gpu.intel.com/xe"
    assert resolved["accelerator"]["type"] == "intel-xe"
    assert resolver._connected is False


def test_explicit_unified_xpu_profile_resolves_dra_driver_without_cluster():
    resolver = ClusterResourceResolver(logger=MagicMock(), dry_run=False)

    resolved = resolver.resolve_all(
        {"accelerator": {"profile": "intel-xpu", "resource": "auto"}}
    )

    assert "resource" not in resolved["accelerator"]
    assert resolved["accelerator"]["draDriver"] == "gpu.intel.com"
    assert resolved["accelerator"]["type"] == "intel-xpu"
    assert resolver._connected is False


def test_unified_xpu_dra_profile_uses_shared_xpu_overlay(tmp_path):
    result, merged = _render(tmp_path, "intel-xpu", None)

    assert not result.has_errors
    assert merged["accelerator"]["type"] == "intel-xpu"
    assert "resource" not in merged["accelerator"]
    assert merged["accelerator"]["draDriver"] == "gpu.intel.com"
    assert "llm-d-xpu" in merged["images"]["vllm"]["repository"]
    assert "--enforce-eager" in merged["decode"]["vllm"]["customCommand"]
    assert merged["dra"]["enabled"] is True
    assert merged["dra"]["claimTemplates"] == {"intel-xpu": {"class": "gpu.intel.com"}}

    plan_dir = result.rendered_paths[0]
    ms_values = yaml.safe_load((plan_dir / "13_ms-values.yaml").read_text())
    assert ms_values["accelerator"]["dra"] is True
    assert ms_values["accelerator"]["resourceClaimTemplates"] == {
        "intel-xpu": {"class": "gpu.intel.com"}
    }

    for rendered in ("13_ms-values.yaml", "14_standalone-deployment_yaml.yaml"):
        # A DRA driver is not an extended resource, so it must never appear
        # as a container resource key.
        assert 'gpu.intel.com: "' not in (plan_dir / rendered).read_text()


def test_cluster_connection_uses_cli_kubeconfig(monkeypatch):
    observed: dict[str, str | None] = {}
    api_client = object()

    def fake_kube_connect(kubeconfig=None, **_kwargs):
        observed["kubeconfig"] = kubeconfig
        return api_client

    monkeypatch.setattr(
        "llmdbenchmark.utilities.cluster.kube_connect", fake_kube_connect
    )
    monkeypatch.setattr("llmdbenchmark.utilities.cluster._KUBE_AVAILABLE", True)

    resolver = ClusterResourceResolver(
        logger=MagicMock(), kubeconfig="/tmp/test-kubeconfig"
    )

    assert resolver._connect(["accelerator.resource"])
    assert resolver._api_client is api_client
    assert observed["kubeconfig"] == "/tmp/test-kubeconfig"


def test_multiple_accelerators_require_explicit_selection():
    resolver = ClusterResourceResolver(logger=MagicMock(), dry_run=False)
    resolver._node_resources = NodeResources(
        accelerator_resources=["gpu.intel.com/xe", "nvidia.com/gpu"]
    )
    values = {"accelerator": {"resource": "auto", "profile": "auto"}}

    try:
        resolver._resolve_accelerator_resource(values, [])
    except RuntimeError as exc:
        assert "Multiple accelerator resources" in str(exc)
    else:
        raise AssertionError("ambiguous accelerator selection must fail")


def test_same_guide_uses_intel_runtime_profile(tmp_path):
    result, merged = _render(tmp_path, "intel-xe", "gpu.intel.com/xe")

    assert not result.has_errors
    assert merged["accelerator"]["type"] == "intel-xe"
    assert "llm-d-xpu" in merged["images"]["vllm"]["repository"]
    assert merged["model"]["name"] == "Qwen/Qwen3-0.6B"
    assert merged["model"]["blockSize"] == 16
    assert merged["decode"]["resources"]["limits"] == {
        "memory": "24Gi",
        "cpu": "8",
    }
    assert merged["decode"]["resources"]["requests"] == {
        "memory": "12Gi",
        "cpu": "4",
    }
    assert merged["decode"]["parallelism"]["tensor"] == 1
    assert merged["storage"]["modelPvc"]["size"] == "50Gi"
    assert merged["storage"]["modelPvc"]["accessModes"] == ["ReadWriteOnce"]
    assert merged["storage"]["workloadPvc"]["accessModes"] == ["ReadWriteOnce"]
    assert merged["harness"]["resources"] == {"cpu": 2, "memory": "8Gi"}
    assert "--enforce-eager" in merged["decode"]["vllm"]["customCommand"]
    assert "--disable-sliding-window" in merged["decode"]["vllm"]["customCommand"]
    assert "--gpu-memory-utilization" in merged["decode"]["vllm"]["customCommand"]
    assert "--block-size $VLLM_BLOCK_SIZE" in merged["decode"]["vllm"]["customCommand"]
    assert "mem_get_info" not in merged["decode"]["vllm"]["customCommand"]
    assert "/tmp/xpu-patch" not in merged["decode"]["vllm"]["customCommand"]
    assert "runtimePreamble" not in merged["accelerator"]
    assert "dtypeArgs" not in merged["accelerator"]
    assert "memoryUtilizationArgs" not in merged["accelerator"]
    assert "blockSizeArgs" not in merged["accelerator"]

    modelservice_values = (result.rendered_paths[0] / "13_ms-values.yaml").read_text()
    assert "gpu.intel.com/xe" in modelservice_values
    assert "ghcr.io/llm-d/llm-d-xpu" in modelservice_values
    assert "supplementalGroups:" not in modelservice_values
    assert "--dtype bfloat16" in modelservice_values


def test_xpu_profile_keeps_precise_router_compact_and_token_optional(tmp_path):
    guide = (
        PROJECT_ROOT
        / "config"
        / "scenarios"
        / "guides"
        / "precise-prefix-cache-routing.yaml"
    )
    result, merged = _render(tmp_path, "intel-xe", "gpu.intel.com/xe", guide)

    assert not result.has_errors
    # assert merged["router"]["epp"]["env"] == []
    assert merged["router"]["epp"]["resources"]["requests"]["cpu"] == "1"
    assert merged["router"]["proxy"]["resources"]["requests"]["cpu"] == "1"
    assert (
        "$(POD_IP):$(VLLM_INFERENCE_PORT)" in merged["decode"]["vllm"]["customCommand"]
    )
    assert "POD_PORT" not in merged["decode"]["vllm"]["customCommand"]


def test_standalone_without_accelerator_labels_uses_resource_scheduling(tmp_path):
    result, _ = _render(
        tmp_path,
        "intel-xe",
        "gpu.intel.com/xe",
        PROJECT_ROOT / "config" / "scenarios" / "examples" / "gpu.yaml",
        setup_overrides={
            "modelservice": {"enabled": False},
            "standalone": {
                "enabled": True,
                "acceleratorType": {"labelKey": "", "labelValue": ""},
            },
        },
    )

    assert not result.has_errors
    deployment_path = result.rendered_paths[0] / "14_standalone-deployment_yaml.yaml"
    deployment_text = deployment_path.read_text()
    deployment = yaml.safe_load(deployment_text)
    pod_spec = deployment["spec"]["template"]["spec"]
    assert "affinity" not in pod_spec
    assert "gpu.intel.com/xe" in deployment_text
    assert "nvidia.com/gpu:None:None" not in deployment_text


@pytest.mark.parametrize("guide_name", XPU_GUIDES)
def test_all_supported_guides_render_from_their_canonical_file(tmp_path, guide_name):
    guide = PROJECT_ROOT / "config" / "scenarios" / "guides" / guide_name
    result, merged = _render(
        tmp_path / guide.stem,
        "intel-xe",
        "gpu.intel.com/xe",
        guide,
    )

    assert not result.has_errors
    assert merged["accelerator"]["profile"] == "intel-xe"
    assert "llm-d-xpu" in merged["images"]["vllm"]["repository"]
    assert merged["model"]["name"] == "Qwen/Qwen3-0.6B"


def test_same_guide_keeps_nvidia_configuration(tmp_path):
    result, merged = _render(tmp_path, "nvidia", "nvidia.com/gpu")

    assert not result.has_errors
    assert merged["accelerator"]["type"] == "nvidia"
    assert "llm-d-xpu" not in merged["images"]["vllm"]["repository"]
    assert merged["model"]["name"] == "Qwen/Qwen3-32B"
    assert "libcuda.so.1" in merged["decode"]["vllm"]["customCommand"]
    assert "--dtype bfloat16" in merged["decode"]["vllm"]["customCommand"]
    assert "--gpu-memory-utilization" in merged["decode"]["vllm"]["customCommand"]
    assert "--block-size $VLLM_BLOCK_SIZE" in merged["decode"]["vllm"]["customCommand"]
    assert "--enforce-eager" not in merged["decode"]["vllm"]["customCommand"]
