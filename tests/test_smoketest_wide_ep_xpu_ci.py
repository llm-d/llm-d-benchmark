"""Regression tests for the aggregated XPU wide-EP CI topology."""

from __future__ import annotations

from llmdbenchmark.smoketests.base import BaseSmoketest


def _config(provider: str, guide: str = "wide-ep") -> dict:
    return {
        "kustomize": {
            "guideName": guide,
            "guideVariableOverrides": {"INFRA_PROVIDER": provider},
        }
    }


def test_xpu_ci_wide_ep_is_detected():
    assert BaseSmoketest._is_xpu_ci_wide_ep(_config("xpu-ci"))


def test_base_wide_ep_is_not_xpu_ci():
    assert not BaseSmoketest._is_xpu_ci_wide_ep(_config("base"))


def test_other_guide_with_xpu_ci_provider_is_not_wide_ep():
    assert not BaseSmoketest._is_xpu_ci_wide_ep(
        _config("xpu-ci", guide="optimized-baseline")
    )


def test_xpu_ci_wide_ep_checks_aggregated_role():
    assert BaseSmoketest._kustomize_roles_to_check(_config("xpu-ci")) == [
        ("prefill-decode", "prefill-decode")
    ]


def test_base_wide_ep_checks_prefill_and_decode_roles():
    assert BaseSmoketest._kustomize_roles_to_check(_config("base")) == [
        ("prefill", "prefill"),
        ("decode", "decode"),
    ]


def test_other_guides_keep_decode_role_default():
    assert BaseSmoketest._kustomize_roles_to_check(
        _config("xpu-ci", guide="optimized-baseline")
    ) == [("decode", "decode")]


def test_pd_disaggregation_keeps_prefill_and_decode_roles():
    assert BaseSmoketest._kustomize_roles_to_check(
        _config("base", guide="pd-disaggregation")
    ) == [("prefill", "prefill"), ("decode", "decode")]


def test_fma_keeps_requester_role_without_role_selector():
    assert BaseSmoketest._kustomize_roles_to_check(
        _config("base", guide="fast-model-actuation-keda")
    ) == [("requester", None)]


def test_xpu_ci_uses_decode_port_for_direct_probe():
    assert BaseSmoketest._direct_inference_port(_config("xpu-ci")) == "8200"


def test_other_providers_use_configured_inference_port():
    config = _config("base")
    config["vllmCommon"] = {"inferencePort": 8123}
    assert BaseSmoketest._direct_inference_port(config) == "8123"
