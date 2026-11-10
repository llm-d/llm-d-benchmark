"""Tests for the EPP-routed inference added to the FMA validator.

The guide path deploys a router the pod-direct checks never exercise; the fma
standup method (``is_fma_only_mode``) deploys none.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport
from llmdbenchmark.smoketests.validators import fma


def _validator(monkeypatch, *, fma_only: bool, baseline_ok: bool):
    """Stubbed FmaValidator; returns (validator, ctx, routed), where routed
    is a spy on the base class's run_inference_test."""
    validator = fma.FmaValidator.__new__(fma.FmaValidator)

    ctx = MagicMock()
    ctx.deployed_methods = ["fma"] if fma_only else ["kustomize"]
    ctx.require_cmd.return_value = MagicMock()
    ctx.require_namespace.return_value = "ns"

    monkeypatch.setattr(
        fma, "_load_config", lambda _p: {"model": {"name": "m"}, "vllmCommon": {}}
    )
    monkeypatch.setattr(validator, "_scaled_to_zero_targets", lambda *a, **k: [])
    monkeypatch.setattr(validator, "_launcher_created_on_demand", lambda *a, **k: False)
    monkeypatch.setattr(
        validator, "_fma_wait_serving", lambda *a, **k: {"metadata": {"name": "l"}}
    )
    monkeypatch.setattr(fma, "_fma_pod_ip", lambda _pod: "10.0.0.1")
    monkeypatch.setattr(
        validator,
        "_fma_request_once",
        lambda *a, **k: (baseline_ok, "ok" if baseline_ok else "boom"),
    )

    routed = MagicMock(return_value=SmoketestReport())
    routed.return_value.add(
        CheckResult("inference_completions", True, message="routed ok")
    )
    monkeypatch.setattr(fma.BaseSmoketest, "run_inference_test", routed)
    return validator, ctx, routed


class TestRoutedInference:
    """Cover the EPP-routing branch of ``run_inference_test``."""

    def test_guide_path_routes_through_the_epp(self, monkeypatch):
        validator, ctx, routed = _validator(
            monkeypatch, fma_only=False, baseline_ok=True
        )

        report = validator.run_inference_test(ctx, Path("stack"))

        assert routed.call_count == 1
        names = [c.name for c in report.checks]
        assert "fma_inference_baseline" in names
        assert "inference_completions" in names  # routed results merged in

    def test_fma_standup_method_stays_pod_direct(self, monkeypatch):
        validator, ctx, routed = _validator(
            monkeypatch, fma_only=True, baseline_ok=True
        )

        report = validator.run_inference_test(ctx, Path("stack"))

        assert routed.call_count == 0
        assert [c.name for c in report.checks] == ["fma_inference_baseline"]

    def test_failed_baseline_skips_the_routed_request(self, monkeypatch):
        validator, ctx, routed = _validator(
            monkeypatch, fma_only=False, baseline_ok=False
        )

        report = validator.run_inference_test(ctx, Path("stack"))

        assert routed.call_count == 0
        assert not report.passed
        assert [c.name for c in report.checks] == ["fma_inference_baseline"]

    def test_failed_routed_request_fails_the_report(self, monkeypatch):
        validator, ctx, routed = _validator(
            monkeypatch, fma_only=False, baseline_ok=True
        )
        routed.return_value = SmoketestReport()
        routed.return_value.add(
            CheckResult("inference_completions", False, message="no healthy upstream")
        )

        report = validator.run_inference_test(ctx, Path("stack"))

        assert not report.passed
