"""Tests for the smoketest inference path's resilience to non-JSON
responses and its handling of HTTP status codes.

Pins two correctness contracts that were missing and caused a
hard-to-diagnose failure on the kind-sim CICD job:

1. **Non-JSON / empty body on /v1/completions triggers chat fallback.**
   The llm-d simulator and modern chat-only model servers respond
   empty (or non-JSON) to legacy /v1/completions. Before this fix,
   _try_completions would fail the smoketest entirely instead of
   trying /v1/chat/completions. The fallback was previously gated on
   `should_fallback: True` which was only set on non-transient JSON
   errors, leaving the most common server-incompatibility case
   uncovered.

2. **HTTP status code is captured by _curl_post.** The previous
   ``curl -sk`` invocation swallowed the response status entirely, so
   an empty 404 / empty 502 / empty 200 all collapsed to "Non-JSON
   response: (empty body)" with no way to distinguish them. We now
   pass ``-w '\\n%{http_code}'`` and split the trailing status off
   in Python; non-2xx returns a meaningful error.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

# Stub planner so we can import smoketest modules (see
# test_smoketest_wait_for_model.py for the same pattern + rationale).
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests.base import BaseSmoketest  # noqa: E402


# ---------------------------------------------------------------------------
# _try_completions: non-JSON / empty body triggers chat fallback
# ---------------------------------------------------------------------------


class TestNonJsonTriggersFallback:
    """Before this fix, an empty body from /v1/completions was a
    terminal smoketest failure. Modern chat-only servers and the
    llm-d simulator both produce this exact pattern, so the fallback
    to /v1/chat/completions has to fire."""

    def _build(self):
        return BaseSmoketest.__new__(BaseSmoketest)

    def _mocks(self):
        cmd = MagicMock()
        cmd.dry_run = False
        ctx = MagicMock()
        return cmd, ctx

    def _patch_curl(self, monkeypatch, body_returned: str):
        """Patch _curl_post to return a fixed body, no error."""
        monkeypatch.setattr(
            BaseSmoketest,
            "_curl_post",
            staticmethod(lambda *a, **kw: (body_returned, None)),
        )

    def _patch_curl_sequence(self, monkeypatch, bodies):
        """Patch _curl_post to return a different body each call.
        Use this to simulate "transient empty -> retry succeeds"."""
        bodies_iter = iter(bodies)
        monkeypatch.setattr(
            BaseSmoketest,
            "_curl_post",
            staticmethod(lambda *a, **kw: (next(bodies_iter), None)),
        )

    def _patch_sleep(self, monkeypatch):
        """Skip the retry_interval sleeps in tests."""
        monkeypatch.setattr(
            "llmdbenchmark.smoketests.base.time.sleep",
            lambda _: None,
        )

    def test_empty_body_returns_should_fallback(self, monkeypatch):
        """Empty body (chat-only server) -> should_fallback=True so the
        caller routes to /v1/chat/completions."""
        cmd, ctx = self._mocks()
        self._patch_curl(monkeypatch, "")
        inst = self._build()
        result = inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "model-name",
            plan_config=None, max_retries=1,
        )
        assert result["success"] is False
        assert result.get("should_fallback") is True, (
            "Empty body must trigger fallback so chat-only servers pass smoketest"
        )
        assert "(empty body)" in result["error"]

    def test_non_json_html_response_returns_should_fallback(self, monkeypatch):
        """E.g. a misconfigured gateway returning an HTML 404 page."""
        cmd, ctx = self._mocks()
        self._patch_curl(monkeypatch, "<html><body>404 Not Found</body></html>")
        inst = self._build()
        result = inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "model-name",
            plan_config=None, max_retries=1,
        )
        assert result.get("should_fallback") is True
        # Body preview is included for debugging
        assert "404 Not Found" in result["error"]

    def test_valid_json_completion_returns_success(self, monkeypatch):
        cmd, ctx = self._mocks()
        body = (
            '{"choices": [{"text": " Washington, D.C."}],'
            '"model": "model-name", "id": "x"}'
        )
        self._patch_curl(monkeypatch, body)
        inst = self._build()
        result = inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "model-name",
            plan_config=None, max_retries=1,
        )
        assert result["success"] is True
        # _try_completions strips the text before returning -- pin that
        # we match the post-strip value (the leading space in the
        # fixture is gone in the result).
        assert result["generated_text"] == "Washington, D.C."
        assert "should_fallback" not in result

    def test_transient_empty_then_valid_succeeds(self, monkeypatch):
        """The kind-sim warmup race: first request returns empty
        body (sim's request handler not yet bound), second request
        returns valid JSON. We MUST retry rather than fall back, so
        the smoketest reports success at /v1/completions and the
        common case stays in the primary code path."""
        cmd, ctx = self._mocks()
        self._patch_sleep(monkeypatch)
        good = (
            '{"choices": [{"text": "ok"}],'
            '"model": "model-name", "id": "x"}'
        )
        self._patch_curl_sequence(monkeypatch, ["", good])
        inst = self._build()
        result = inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "model-name",
            plan_config=None, max_retries=3, retry_interval=0,
        )
        assert result["success"] is True
        assert result["generated_text"] == "ok"
        assert "should_fallback" not in result
        # The "retrying in ..." log line fired on attempt 1
        assert any(
            "retrying" in str(c) for c in ctx.logger.log_info.call_args_list
        )

    def test_exhausted_retries_then_fallback(self, monkeypatch):
        """All retries return empty (server genuinely doesn't speak
        /v1/completions). After max_retries, return
        should_fallback=True so chat fallback kicks in."""
        cmd, ctx = self._mocks()
        self._patch_sleep(monkeypatch)
        self._patch_curl_sequence(monkeypatch, ["", "", ""])
        inst = self._build()
        result = inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "model-name",
            plan_config=None, max_retries=3, retry_interval=0,
        )
        assert result["success"] is False
        assert result.get("should_fallback") is True
        assert "(empty body)" in result["error"]

    def test_transient_partial_then_valid_succeeds(self, monkeypatch):
        """Connection cut mid-stream gives partial JSON. Same handling
        as empty: retry, succeed on next attempt."""
        cmd, ctx = self._mocks()
        self._patch_sleep(monkeypatch)
        good = (
            '{"choices": [{"text": "ok"}],'
            '"model": "model-name", "id": "x"}'
        )
        self._patch_curl_sequence(
            monkeypatch, ['{"choices": [{"text', good],
        )
        inst = self._build()
        result = inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "model-name",
            plan_config=None, max_retries=3, retry_interval=0,
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Exponential backoff between retries
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """Waits between retries should grow so a slow-to-warm server gets
    more time on later attempts. With base=15s, cap=120s:
        attempt 1 -> 15s, 2 -> 30s, 3 -> 60s, 4 -> 120s, 5 -> 120s (capped)
    Total budget over 5 attempts: ~225s (3.75 min)."""

    @pytest.mark.parametrize(
        "attempt,expected",
        [
            (1, 15),    # base
            (2, 30),    # 2x
            (3, 60),    # 4x
            (4, 120),   # 8x, hits cap
            (5, 120),   # cap
            (6, 120),   # cap (defensive)
        ],
    )
    def test_compute_backoff_sequence(self, attempt, expected):
        assert BaseSmoketest._compute_backoff(attempt, 15, 120) == expected

    def test_compute_backoff_below_attempt_1_returns_base(self):
        """Defensive: garbage input falls back to the base interval."""
        assert BaseSmoketest._compute_backoff(0, 15, 120) == 15
        assert BaseSmoketest._compute_backoff(-3, 15, 120) == 15

    def test_compute_backoff_respects_cap(self):
        """Cap below base is honored even when 2^0 * base would exceed it
        (edge case, but pins the cap-wins-on-tie semantic)."""
        assert BaseSmoketest._compute_backoff(1, 100, 50) == 50

    def test_default_retry_budget_pinned(self):
        """If someone reverts to the old 3-attempt / 15s-flat budget we
        want a loud failure. Old default gave only 30s of total wait,
        too short for warmup races on slower clusters."""
        assert BaseSmoketest._DEFAULT_INFERENCE_MAX_RETRIES == 5
        assert BaseSmoketest._DEFAULT_INFERENCE_RETRY_BASE == 15
        assert BaseSmoketest._DEFAULT_INFERENCE_RETRY_MAX == 120

    def test_actual_backoff_used_in_completions_loop(self, monkeypatch):
        """The sleep durations called during a real retry sequence
        match the backoff schedule, not the flat retry_interval."""
        cmd = MagicMock()
        cmd.dry_run = False
        ctx = MagicMock()
        # Always-empty stdout, no err -> drives JSON-decode-failure
        # branch on every attempt and exhausts retries.
        monkeypatch.setattr(
            BaseSmoketest,
            "_curl_post",
            staticmethod(lambda *a, **kw: ("", None)),
        )
        sleeps: list[int] = []
        monkeypatch.setattr(
            "llmdbenchmark.smoketests.base.time.sleep",
            lambda s: sleeps.append(s),
        )
        inst = BaseSmoketest.__new__(BaseSmoketest)
        inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "m",
            plan_config=None,
            max_retries=4,
            retry_interval=15,
            retry_max_interval=120,
        )
        # 4 attempts -> 3 sleeps in between (no sleep after final attempt)
        assert sleeps == [15, 30, 60]

    def test_plan_config_can_lengthen_retry_budget(self, monkeypatch):
        """A scenario shipping a slow-to-warm model can crank the budget
        without touching code."""
        cmd = MagicMock()
        cmd.dry_run = False
        ctx = MagicMock()
        monkeypatch.setattr(
            BaseSmoketest,
            "_curl_post",
            staticmethod(lambda *a, **kw: ("", None)),
        )
        sleeps: list[int] = []
        monkeypatch.setattr(
            "llmdbenchmark.smoketests.base.time.sleep",
            lambda s: sleeps.append(s),
        )
        plan_config = {
            "harness": {
                "smoketest": {
                    "inferenceMaxRetries": 3,
                    "inferenceRetryBaseInterval": 30,
                    "inferenceRetryMaxInterval": 60,
                },
            },
        }
        inst = BaseSmoketest.__new__(BaseSmoketest)
        inst._try_completions(
            cmd, ctx, "ns", "http://svc:80", "m",
            plan_config=plan_config,
        )
        # 3 attempts -> 2 sleeps: 30s, 60s (2x base hits the 60s cap)
        assert sleeps == [30, 60]


# ---------------------------------------------------------------------------
# _curl_post: HTTP status code parsing
# ---------------------------------------------------------------------------


class TestCurlPostStatusParsing:
    """The new ``-w '\\n%{http_code}'`` flag means stdout ends with the
    status on its own line. Tests pin the split logic."""

    def _mock_kube_result(self, stdout: str, success: bool = True):
        result = MagicMock()
        result.success = success
        result.stdout = stdout
        result.stderr = ""
        result.dry_run = False
        return result

    def _make_cmd(self, kube_result):
        cmd = MagicMock()
        cmd.kube = MagicMock(return_value=kube_result)
        return cmd

    def test_2xx_with_body_returns_body_no_error(self):
        # JSON body + trailing 200
        stdout = '{"choices":[{"text":"hi"}]}\n200'
        cmd = self._make_cmd(self._mock_kube_result(stdout))
        body, err = BaseSmoketest._curl_post(
            cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
        )
        assert err is None
        assert body == '{"choices":[{"text":"hi"}]}'

    def test_2xx_empty_body_returns_empty_no_error(self):
        # Some servers legitimately return empty 2xx; treat as success
        # at the curl layer -- the JSON parser later flags it as
        # non-JSON and triggers the fallback.
        stdout = "\n200"
        cmd = self._make_cmd(self._mock_kube_result(stdout))
        body, err = BaseSmoketest._curl_post(
            cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
        )
        assert err is None
        assert body == ""

    def test_404_empty_body_returns_diagnosable_error(self):
        """An empty 404 (e.g. server doesn't speak this endpoint at all)
        previously disappeared into 'Non-JSON: (empty body)'. Now it
        surfaces the actual status code."""
        stdout = "\n404"
        cmd = self._make_cmd(self._mock_kube_result(stdout))
        body, err = BaseSmoketest._curl_post(
            cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
        )
        assert err is not None
        assert "HTTP 404" in err
        assert "/v1/completions" in err

    def test_502_with_envoy_body_includes_both_status_and_body(self):
        """Envoy returns a status + body when upstream is unreachable;
        both should be surfaced for diagnosis."""
        stdout = (
            "upstream connect error or disconnect/reset before headers. "
            "reset reason: connection refused\n502"
        )
        cmd = self._make_cmd(self._mock_kube_result(stdout))
        body, err = BaseSmoketest._curl_post(
            cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
        )
        assert err is not None
        assert "HTTP 502" in err
        assert "upstream connect error" in err

    def test_5xx_distinct_from_4xx_in_error_text(self):
        """Just a regression guard that we're not bucketing 5xx into 4xx."""
        for status in ("500", "502", "503", "504"):
            stdout = f"server error\n{status}"
            cmd = self._make_cmd(self._mock_kube_result(stdout))
            body, err = BaseSmoketest._curl_post(
                cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
            )
            assert err is not None
            assert f"HTTP {status}" in err

    def test_curl_subprocess_failure_returns_original_error_shape(self):
        """If kubectl itself fails (network, RBAC, etc.) we still report
        a usable error -- this path predates the status-code split."""
        bad_result = self._mock_kube_result("", success=False)
        bad_result.stderr = "Error from server (Forbidden): pods is forbidden"
        cmd = self._make_cmd(bad_result)
        body, err = BaseSmoketest._curl_post(
            cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
        )
        assert body == ""
        assert err is not None
        assert "Forbidden" in err

    def test_dry_run_short_circuits(self):
        result = MagicMock()
        result.dry_run = True
        cmd = self._make_cmd(result)
        body, err = BaseSmoketest._curl_post(
            cmd, "ns", "http://svc/v1/completions", {"x": 1}, plan_config=None,
        )
        assert body == ""
        assert err is None
