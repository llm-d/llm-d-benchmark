"""Tests for the cluster-free (nok8s) smoketest probes.

Issue #1698: `llmdbenchmark ... smoketest` on a nok8s scenario died before
step 00 with a kubeconfig error, because `_do_smoketest` never set
`container_only`, and even past that the validators were cluster-shaped
(Service IP discovery, pod readiness, `oc get route`).

These tests drive the probes against a real stdlib HTTP server on an
ephemeral loopback port, so the request path (socket, status code, JSON
body) is exercised for real rather than mocked. The failure paths matter
most: a probe that silently passes when the endpoint is down is worse than
no probe.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Stub planner so we can import smoketest modules (see
# test_smoketest_inference.py for the same pattern + rationale).
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.smoketests import nok8s  # noqa: E402

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"


# ---------------------------------------------------------------------------
# A configurable OpenAI-shaped stub server
# ---------------------------------------------------------------------------


class _Responses:
    """Per-test response config, mutated by the tests before each request."""

    def __init__(self):
        self.models = (200, {"data": [{"id": MODEL}]})
        self.completions = (200, {"choices": [{"text": " world"}]})
        # One-shot reply served before `models`, for the retry test.
        self.models_once = None
        self.model_hits = 0


def _make_handler(responses: _Responses):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status, body):
            payload = (
                body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/v1/models":
                responses.model_hits += 1
                once, responses.models_once = responses.models_once, None
                self._reply(*(once or responses.models))
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if self.path == "/v1/completions":
                self._reply(*responses.completions)
            else:
                self._reply(404, {"error": "not found"})

        def log_message(self, *args):  # silence the stub's stderr noise
            pass

    return Handler


@pytest.fixture(autouse=True)
def _no_retry_backoff(monkeypatch):
    """Keep the suite quick; the retry budget has its own explicit test."""
    monkeypatch.setattr(nok8s, "_RETRY_TOTAL", 0)
    monkeypatch.setattr(nok8s, "_RETRY_BACKOFF", 0)


@pytest.fixture
def server():
    """A running stub server; yields (responses, port)."""
    responses = _Responses()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(responses))
    # Short poll interval: shutdown() otherwise costs 0.5s per test.
    thread = threading.Thread(target=httpd.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield responses, httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _closed_port() -> int:
    """Bind and immediately release a port so nothing is listening on it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stack_dir(tmp_path: Path, port: int, *, model: str = MODEL) -> Path:
    """A stack dir holding a real 34_nok8s-containers.yaml launch spec."""
    stack = tmp_path / "nok8s-single"
    stack.mkdir()
    (stack / "34_nok8s-containers.yaml").write_text(
        f'runtime: docker\nmodel: {model}\nendpoint: "http://127.0.0.1:{port}"\n',
        encoding="utf-8",
    )
    return stack


def _context(dry_run: bool = False):
    ctx = MagicMock()
    ctx.container_only = True
    ctx.dry_run = dry_run
    ctx.logger = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_happy_path(self, server, tmp_path):
        _responses, port = server
        report = nok8s.health_check(_context(), _stack_dir(tmp_path, port))
        assert report.passed, report.errors()
        assert {c.name for c in report.checks} == {
            "nok8s_models_endpoint",
            "nok8s_model_served",
        }

    def test_unreachable_endpoint_fails(self, tmp_path):
        report = nok8s.health_check(_context(), _stack_dir(tmp_path, _closed_port()))
        assert not report.passed
        assert "unreachable" in "".join(report.errors())

    def test_non_200_fails(self, server, tmp_path):
        responses, port = server
        responses.models = (503, {"error": "unavailable"})
        report = nok8s.health_check(_context(), _stack_dir(tmp_path, port))
        assert not report.passed
        assert "503" in "".join(report.errors())

    def test_different_model_fails_and_names_expected(self, server, tmp_path):
        responses, port = server
        responses.models = (200, {"data": [{"id": "some/other-model"}]})
        report = nok8s.health_check(_context(), _stack_dir(tmp_path, port))
        assert not report.passed
        errors = "".join(report.errors())
        assert MODEL in errors and "some/other-model" in errors

    @pytest.mark.parametrize(
        "body",
        [b"<html>gateway</html>", b"null", b'["a"]'],
        ids=["html", "null", "list"],
    )
    def test_body_that_is_not_a_json_object_fails(self, server, tmp_path, body):
        responses, port = server
        responses.models = (200, body)
        report = nok8s.health_check(_context(), _stack_dir(tmp_path, port))
        assert not report.passed
        assert "did not return a JSON object" in "".join(report.errors())

    def test_transient_503_is_retried(self, server, tmp_path, monkeypatch):
        responses, port = server
        monkeypatch.setattr(nok8s, "_RETRY_TOTAL", 2)
        responses.models_once = (503, {"error": "warming up"})
        report = nok8s.health_check(_context(), _stack_dir(tmp_path, port))
        assert report.passed, report.errors()
        assert responses.model_hits == 2

    def test_missing_spec_file_fails(self, tmp_path):
        empty = tmp_path / "nok8s-single"
        empty.mkdir()
        report = nok8s.health_check(_context(), empty)
        assert not report.passed
        assert "34_nok8s-containers" in "".join(report.errors())

    def test_dry_run_issues_no_http(self, tmp_path):
        # Port is closed: a real request would fail, so a pass proves no I/O.
        stack = _stack_dir(tmp_path, _closed_port())
        report = nok8s.health_check(_context(dry_run=True), stack)
        assert report.passed
        assert report.total == 0


# ---------------------------------------------------------------------------
# inference_test
# ---------------------------------------------------------------------------


class TestInferenceTest:
    def test_happy_path(self, server, tmp_path):
        _responses, port = server
        report = nok8s.inference_test(_context(), _stack_dir(tmp_path, port))
        assert report.passed, report.errors()

    def test_server_error_fails(self, server, tmp_path):
        responses, port = server
        responses.completions = (500, {"error": "boom"})
        report = nok8s.inference_test(_context(), _stack_dir(tmp_path, port))
        assert not report.passed
        assert "500" in "".join(report.errors())

    @pytest.mark.parametrize(
        "body",
        [{"choices": []}, {"choices": ["abc"]}, {"choices": [{"text": ""}]}, {}],
        ids=["empty", "strings", "blank-text", "no-choices"],
    )
    def test_missing_generated_text_fails(self, server, tmp_path, body):
        responses, port = server
        responses.completions = (200, body)
        report = nok8s.inference_test(_context(), _stack_dir(tmp_path, port))
        assert not report.passed
        assert "no generated text" in "".join(report.errors())

    @pytest.mark.parametrize("body", [b"null", b'["a"]'], ids=["null", "list"])
    def test_body_that_is_not_a_json_object_fails(self, server, tmp_path, body):
        responses, port = server
        responses.completions = (200, body)
        report = nok8s.inference_test(_context(), _stack_dir(tmp_path, port))
        assert not report.passed
        assert "did not return a JSON object" in "".join(report.errors())

    def test_unreachable_endpoint_fails(self, tmp_path):
        report = nok8s.inference_test(_context(), _stack_dir(tmp_path, _closed_port()))
        assert not report.passed
        assert "unreachable" in "".join(report.errors())

    def test_dry_run_issues_no_http(self, tmp_path):
        stack = _stack_dir(tmp_path, _closed_port())
        report = nok8s.inference_test(_context(dry_run=True), stack)
        assert report.passed
        assert report.total == 0


# ---------------------------------------------------------------------------
# The base validator must route container_only deployments to the probes
# instead of the cluster-shaped path (which calls require_cmd() first).
# ---------------------------------------------------------------------------


class TestBaseRoutesContainerOnly:
    def _validator(self):
        from llmdbenchmark.smoketests.base import BaseSmoketest

        return BaseSmoketest.__new__(BaseSmoketest)

    def test_health_checks_do_not_touch_the_cluster(self, server, tmp_path):
        _responses, port = server
        ctx = _context()
        ctx.require_cmd.side_effect = AssertionError("must not require kubectl")
        ctx.require_namespace.side_effect = AssertionError("must not need namespace")
        report = self._validator().run_health_checks(ctx, _stack_dir(tmp_path, port))
        assert report.passed, report.errors()

    def test_inference_does_not_touch_the_cluster(self, server, tmp_path):
        _responses, port = server
        ctx = _context()
        ctx.require_cmd.side_effect = AssertionError("must not require kubectl")
        ctx.require_namespace.side_effect = AssertionError("must not need namespace")
        report = self._validator().run_inference_test(ctx, _stack_dir(tmp_path, port))
        assert report.passed, report.errors()


# ---------------------------------------------------------------------------
# cli._do_smoketest must set container_only so resolve_cluster() short-circuits
# instead of raising "Failed to load Kubernetes configuration".
# ---------------------------------------------------------------------------


def test_do_smoketest_sets_container_only(monkeypatch, tmp_path):
    from llmdbenchmark import cli

    monkeypatch.setattr(cli.config, "plan_dir", tmp_path, raising=False)
    monkeypatch.setattr(cli.config, "workspace", tmp_path, raising=False)
    monkeypatch.setattr(cli, "_load_all_stacks_info", lambda paths: [{}])
    monkeypatch.setattr(cli, "_resolve_deploy_methods", lambda *a, **kw: ["nok8s"])
    monkeypatch.setattr(cli, "_parse_namespaces", lambda *a, **kw: ("llmdbench", None))

    captured = {}

    class _Result:
        has_errors = False

    class _Executor:
        def __init__(self, **kwargs):
            captured["context"] = kwargs["context"]

        def execute(self, step_spec=None):
            return _Result()

    monkeypatch.setattr(cli, "StepExecutor", _Executor)
    monkeypatch.setattr(cli, "get_smoketest_steps", lambda: [])

    args = types.SimpleNamespace()
    cli._do_smoketest(args, MagicMock(), types.SimpleNamespace(rendered_paths=[]))

    assert captured["context"].container_only is True

    # resolve_cluster() must short-circuit on container_only instead of
    # reaching the kubeconfig-loading resolver (which is what issue #1698
    # tripped over). Fail loudly if it ever gets there.
    import llmdbenchmark.utilities.cluster as cluster_mod

    monkeypatch.setattr(
        cluster_mod,
        "resolve_cluster",
        lambda ctx: pytest.fail("smoketest must not resolve a cluster for nok8s"),
    )
    captured["context"].resolve_cluster()
