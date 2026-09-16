"""Tests for connector folding in `GuideVariableResolver.effective_backend`.

The modelserver overlay is routed at `modelserver/{acceleratorBackend}/{connector}/{infra}`.
The connector is read from `kustomize.guideVariableOverrides.CONNECTOR` -- the
same value the CI workflow writes for the guide README's `${CONNECTOR}`
substitution -- falling back to the legacy first-class `kustomize.connector`
key that the workflow (and older scenarios) still set.
"""

from __future__ import annotations

from llmdbenchmark.kustomize.variable_resolver import GuideVariableResolver


def test_connector_override_is_spliced():
    kust = {
        "acceleratorBackend": "amd/vllm",
        "guideVariableOverrides": {"CONNECTOR": "moriio"},
    }
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm/moriio"


def test_falls_back_to_legacy_first_class_connector():
    kust = {"acceleratorBackend": "amd/vllm", "connector": "moriio"}
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm/moriio"


def test_override_takes_precedence_over_legacy_key():
    kust = {
        "acceleratorBackend": "amd/vllm",
        "connector": "nixlv2",
        "guideVariableOverrides": {"CONNECTOR": "moriio"},
    }
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm/moriio"


def test_null_guide_variable_overrides_does_not_crash():
    kust = {"acceleratorBackend": "amd/vllm", "guideVariableOverrides": None}
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm"


def test_null_overrides_falls_back_to_legacy_key():
    kust = {
        "acceleratorBackend": "amd/vllm",
        "guideVariableOverrides": None,
        "connector": "moriio",
    }
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm/moriio"


def test_no_connector_returns_bare_backend():
    kust = {"acceleratorBackend": "amd/vllm"}
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm"


def test_empty_override_connector_is_ignored():
    kust = {
        "acceleratorBackend": "amd/vllm",
        "guideVariableOverrides": {"CONNECTOR": ""},
    }
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm"


def test_connector_surrounding_slashes_are_trimmed():
    kust = {
        "acceleratorBackend": "amd/vllm",
        "guideVariableOverrides": {"CONNECTOR": "/moriio/"},
    }
    assert GuideVariableResolver.effective_backend(kust) == "amd/vllm/moriio"


def test_resolve_does_not_corrupt_suffixed_backend_paths(tmp_path):
    resolver = GuideVariableResolver(
        guide_name="wide-ep-lws",
        namespace="test-ns",
        gaie_version="v1.6.0",
        repo_path=str(tmp_path),
        accelerator_backend="xpu/vllm",
        variable_overrides={"INFRA_PROVIDER": "gke"},
    )
    gpu_suffixed = resolver.resolve(
        "kubectl apply -k modelserver/gpu/vllm-deepseek-r1-0528/${INFRA_PROVIDER}"
    )
    assert gpu_suffixed == "kubectl apply -k modelserver/gpu/vllm-deepseek-r1-0528/gke"

    standard = resolver.resolve(
        "kubectl apply -k modelserver/gpu/vllm/${INFRA_PROVIDER}"
    )
    assert standard == "kubectl apply -k modelserver/xpu/vllm/gke"
