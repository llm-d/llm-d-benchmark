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
