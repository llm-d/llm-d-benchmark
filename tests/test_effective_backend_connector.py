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


def test_tpu_v5p_sglang_resolves_modelserver_overlay_path(tmp_path):
    kust = {"acceleratorBackend": "tpu/v5p/sglang"}
    resolver = GuideVariableResolver(
        guide_name="optimized-baseline",
        namespace="test-ns",
        gaie_version="v1.6.1",
        repo_path=str(tmp_path),
        accelerator_backend=GuideVariableResolver.effective_backend(kust),
        variable_overrides={"INFRA_PROVIDER": "gke"},
        readme_variables={
            "ACCELERATOR_TYPE": "gpu",
            "MODEL_SERVER": "vllm",
            "INFRA_PROVIDER": "base",
        },
    )
    resolved = resolver.resolve(
        "kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/${ACCELERATOR_TYPE}/${MODEL_SERVER}/${INFRA_PROVIDER}/"
    )
    assert resolved.endswith(
        "guides/optimized-baseline/modelserver/tpu/v5p/sglang/gke/"
    )
