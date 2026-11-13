"""Tests for ensure_service_account and its wiring into ephemeral curl pods.

The FMA+KEDA GKE lane hit ``serviceaccount "inference-perf-runner" not
found`` at pod admission: nothing creates the plan's SA on a fresh namespace
before the smoketest.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from llmdbenchmark.smoketests.base import BaseSmoketest
from llmdbenchmark.utilities import endpoint
from llmdbenchmark.utilities.endpoint import ensure_service_account


class FakeResult:
    def __init__(self, success=True, stdout="", stderr="", dry_run=False):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.dry_run = dry_run


class FakeCmd:
    """Records kube() calls; "get" misses unless gets_succeed=True."""

    def __init__(self, gets_succeed=False, run_result=None):
        self.calls: list[tuple[str, ...]] = []
        self.gets_succeed = gets_succeed
        self.run_result = run_result or FakeResult()
        self.logger = MagicMock()

    def kube(self, *args: str, **kwargs: Any) -> FakeResult:
        self.calls.append(tuple(args))
        if args[:2] == ("get", "pods"):
            return FakeResult(success=True, stdout="10.0.0.1")
        if args[0] == "get":
            return FakeResult(success=self.gets_succeed)
        if args[0] == "run":
            return self.run_result
        return FakeResult()


PLAN = {"serviceAccount": {"name": "inference-perf-runner"}}


def test_default_creates_sa_only():
    cmd = FakeCmd()
    ensure_service_account(cmd, "ns", PLAN)
    verbs = [(c[0], c[1]) for c in cmd.calls]
    assert ("create", "sa") in verbs
    assert ("create", "role") not in verbs
    assert ("create", "rolebinding") not in verbs


def test_with_rbac_creates_role_and_binding():
    cmd = FakeCmd()
    ensure_service_account(cmd, "ns", PLAN, with_rbac=True)
    verbs = [(c[0], c[1]) for c in cmd.calls]
    assert ("create", "sa") in verbs
    assert ("create", "role") in verbs
    assert ("create", "rolebinding") in verbs


def test_existing_sa_skips_creation():
    cmd = FakeCmd(gets_succeed=True)
    ensure_service_account(cmd, "ns", PLAN)
    assert all(c[0] != "create" for c in cmd.calls)


def test_no_sa_configured_is_a_no_op():
    cmd = FakeCmd()
    ensure_service_account(cmd, "ns", {})
    ensure_service_account(cmd, "ns", None, "default")
    assert cmd.calls == []


def test_curl_post_ensures_sa_before_running_pod():
    cmd = FakeCmd(
        run_result=FakeResult(success=True, stdout='{"generated_text": "ok"}\n200')
    )
    BaseSmoketest._curl_post(
        cmd, "ns", "http://x:80/v1/completions", {"model": "m"}, PLAN
    )
    first_run = next(i for i, c in enumerate(cmd.calls) if c[0] == "run")
    sa_create = next(i for i, c in enumerate(cmd.calls) if c[:2] == ("create", "sa"))
    assert sa_create < first_run
    # A probe grants no RBAC.
    assert not any(c[:2] == ("create", "role") for c in cmd.calls)


def test_curl_post_without_sa_configured_skips_ensure():
    cmd = FakeCmd(
        run_result=FakeResult(success=True, stdout='{"generated_text": "ok"}\n200')
    )
    BaseSmoketest._curl_post(
        cmd, "ns", "http://x:80/v1/completions", {"model": "m"}, {}
    )
    assert all(c[0] != "create" for c in cmd.calls)


def test_check_health_ensures_sa():
    cmd = FakeCmd(run_result=FakeResult(success=True, stdout="200"))
    smoke = BaseSmoketest.__new__(BaseSmoketest)
    smoke._check_health(cmd, MagicMock(), "ns", "h", "80", PLAN)
    assert any(c[:2] == ("create", "sa") for c in cmd.calls)


def test_model_serving_still_ensures_after_extraction(monkeypatch):
    spy = MagicMock()
    monkeypatch.setattr(endpoint, "ensure_service_account", spy)
    cmd = FakeCmd(run_result=FakeResult(success=True, stdout='{"data": [{"id": "m"}]}'))
    result = endpoint.test_model_serving(cmd, "ns", "h", "80", "m", PLAN)
    assert result is None
    spy.assert_called_once_with(cmd, "ns", PLAN, None, with_rbac=True)


def test_reset_caches_ensures_sa_before_running_pod():
    cmd = FakeCmd(
        run_result=FakeResult(
            success=True, stdout='== 10.0.0.1/reset ==\n{"success": true}\n200\n'
        )
    )
    endpoint.reset_caches_pods(cmd, "ns", "m", "8000", plan_config=PLAN, max_retries=1)
    first_run = next(i for i, c in enumerate(cmd.calls) if c[0] == "run")
    sa_create = next(i for i, c in enumerate(cmd.calls) if c[:2] == ("create", "sa"))
    assert sa_create < first_run
    # A probe grants no RBAC.
    assert not any(c[:2] == ("create", "role") for c in cmd.calls)
