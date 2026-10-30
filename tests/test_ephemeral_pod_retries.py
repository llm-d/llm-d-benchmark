"""A failed image pull must not fail the step: retry with a new pod."""

from __future__ import annotations

from unittest.mock import MagicMock

from llmdbenchmark.smoketests.base import BaseSmoketest
from llmdbenchmark.utilities.endpoint import is_pod_start_failure
from llmdbenchmark.utilities.endpoint import reset_caches_pods
from llmdbenchmark.utilities.endpoint import test_model_serving as run_model_serving

WAIT_TIMEOUT = "error: timed out waiting for the condition"
FORBIDDEN = "Error from server (Forbidden): pods is forbidden"


def _kube_result(stdout: str = "", stderr: str = "", success: bool = True):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.success = success
    result.dry_run = False
    return result


class _FakeKube:
    """cmd.kube stand-in: canned results for ``run``, fixed result elsewhere."""

    def __init__(self, run_results, get_result=None):
        self.run_results = list(run_results)
        self.run_calls = 0
        self.get_result = get_result or _kube_result()

    def __call__(self, *args, check=False):
        if args and args[0] == "run":
            self.run_calls += 1
            return self.run_results.pop(0)
        if args and args[0] == "get":
            return self.get_result
        return _kube_result()


def _fake_cmd(run_results) -> tuple[MagicMock, _FakeKube]:
    kube = _FakeKube(run_results)
    cmd = MagicMock()
    cmd.kube = kube
    return cmd, kube


def test_pod_start_failure_detected():
    assert is_pod_start_failure(WAIT_TIMEOUT)


def test_other_failures_not_detected():
    assert not is_pod_start_failure(FORBIDDEN)
    assert not is_pod_start_failure("")


def test_curl_post_retries_pull_failure(monkeypatch):
    monkeypatch.setattr("llmdbenchmark.smoketests.base.time.sleep", lambda _: None)
    cmd, kube = _fake_cmd(
        [
            _kube_result(stderr=WAIT_TIMEOUT, success=False),
            _kube_result('{"choices":[{"text":"hi"}]}\n200'),
        ]
    )
    body, err = BaseSmoketest._curl_post(
        cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None
    )
    assert err is None
    assert kube.run_calls == 2


def test_curl_post_gives_up_after_three_pods(monkeypatch):
    monkeypatch.setattr("llmdbenchmark.smoketests.base.time.sleep", lambda _: None)
    cmd, kube = _fake_cmd([_kube_result(stderr=WAIT_TIMEOUT, success=False)] * 3)
    _, err = BaseSmoketest._curl_post(
        cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None
    )
    assert err is not None
    assert kube.run_calls == 3


def test_curl_post_does_not_retry_other_failures():
    cmd, kube = _fake_cmd([_kube_result(stderr=FORBIDDEN, success=False)])
    _, err = BaseSmoketest._curl_post(
        cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None
    )
    assert "Forbidden" in err
    assert kube.run_calls == 1


def test_model_serving_retries_pull_failure(monkeypatch):
    monkeypatch.setattr("llmdbenchmark.utilities.endpoint.time.sleep", lambda _: None)
    cmd, kube = _fake_cmd(
        [
            _kube_result(stderr=WAIT_TIMEOUT, success=False),
            _kube_result('{"data": [{"id": "m"}]}'),
        ]
    )
    error = run_model_serving(
        cmd, "ns", "svc", 80, "m", plan_config=None, max_retries=3, retry_interval=0
    )
    assert error is None
    assert kube.run_calls == 2


def test_model_serving_fails_fast_on_persistent_pull_failure(monkeypatch):
    monkeypatch.setattr("llmdbenchmark.utilities.endpoint.time.sleep", lambda _: None)
    cmd, kube = _fake_cmd([_kube_result(stderr=WAIT_TIMEOUT, success=False)] * 3)
    error = run_model_serving(
        cmd, "ns", "svc", 80, "m", plan_config=None, max_retries=12, retry_interval=0
    )
    assert error is not None
    assert kube.run_calls == 3


def test_reset_caches_retries_pull_failure(monkeypatch):
    """reset_caches_required makes any warning fatal, so a transient pull
    failure must not produce one."""
    monkeypatch.setattr("llmdbenchmark.utilities.endpoint.time.sleep", lambda _: None)
    kube = _FakeKube(
        [
            _kube_result(stderr=WAIT_TIMEOUT, success=False),
            _kube_result(""),
        ],
        get_result=_kube_result("10.0.0.1"),
    )
    cmd = MagicMock()
    cmd.kube = kube
    warnings = reset_caches_pods(cmd, "ns", "m", 8000)
    assert warnings == []
    assert kube.run_calls == 2


def test_reset_caches_warns_after_three_pods(monkeypatch):
    monkeypatch.setattr("llmdbenchmark.utilities.endpoint.time.sleep", lambda _: None)
    kube = _FakeKube(
        [_kube_result(stderr=WAIT_TIMEOUT, success=False)] * 3,
        get_result=_kube_result("10.0.0.1"),
    )
    cmd = MagicMock()
    cmd.kube = kube
    warnings = reset_caches_pods(cmd, "ns", "m", 8000)
    assert len(warnings) == 1
    assert "curl pod failed" in warnings[0]
    assert kube.run_calls == 3
