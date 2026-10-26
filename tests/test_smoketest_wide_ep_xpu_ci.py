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


def test_xpu_ci_uses_decode_port_for_direct_probe():
    assert BaseSmoketest._direct_inference_port(_config("xpu-ci")) == "8200"


def test_other_providers_use_configured_inference_port():
    config = _config("base")
    config["vllmCommon"] = {"inferencePort": 8123}
    assert BaseSmoketest._direct_inference_port(config) == "8123"
