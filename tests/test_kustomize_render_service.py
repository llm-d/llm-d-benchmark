"""Render Service deployment and API checks for kustomize guides."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.kustomize.readme_parser import CommandPhase, parse_guide_readme
from llmdbenchmark.kustomize.render_probe import (
    _RENDER_PROBE_SCRIPT,
    probe_render_service,
)

# Import the step without loading unrelated standup steps and their optional
# planner dependency.
_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "standup"
    / "steps"
    / "step_05_kustomize_deploy.py"
)
_spec = importlib.util.spec_from_file_location("kustomize_render_deploy", _STEP_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["kustomize_render_deploy"] = _module
_spec.loader.exec_module(_module)
KustomizeDeployStep = _module.KustomizeDeployStep


@dataclass
class FakeResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@pytest.mark.parametrize("render_before_modelserver", [False, True])
def test_readme_parser_separates_render_from_modelserver_and_router(
    tmp_path: Path, render_before_modelserver: bool
):
    router = "# Deploy the llm-d Router\n```bash\nhelm install demo chart\n```\n"
    modelserver = (
        "# Deploy the Model Server\n"
        "```bash\nkubectl apply -k modelserver/gpu/vllm/base\n```\n"
    )
    render = (
        "# Deploy and Check the Render (Tokenizer) Service\n"
        "```bash\nkubectl apply -k render/\n```\n"
        "<!-- llm-d-cicd:skip start -->\n"
        "```bash\nkubectl apply -k render/standalone/\n```\n"
        "<!-- llm-d-cicd:skip end -->\n"
        "## Verify the render Service\n"
        "```bash\nkubectl get service render\n```\n"
    )
    readme = tmp_path / "README.md"
    readme.write_text(
        router
        + (render + modelserver if render_before_modelserver else modelserver + render),
        encoding="utf-8",
    )

    parsed = parse_guide_readme(readme)

    assert [command.raw for command in parsed.get_commands(CommandPhase.ROUTER)] == [
        "helm install demo chart"
    ]
    assert [
        command.raw for command in parsed.get_commands(CommandPhase.MODELSERVER)
    ] == ["kubectl apply -k modelserver/gpu/vllm/base"]
    assert [command.raw for command in parsed.get_commands(CommandPhase.RENDER)] == [
        "kubectl apply -k render/"
    ]
    assert "kubectl apply -k render/" in [
        command.raw for command in parsed.get_deploy_commands()
    ]


@pytest.mark.parametrize("probe_error", [None, "render endpoint returned 404"])
def test_kustomize_standup_applies_and_probes_render_after_modelserver_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_error: str | None
):
    guide_dir = tmp_path / "guides" / "demo"
    guide_dir.mkdir(parents=True)
    (guide_dir / "README.md").write_text(
        "# Deploy the Model Server\n"
        "```bash\n"
        "kubectl apply -n ${NAMESPACE} -k "
        "${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/base/\n"
        "```\n"
        "# Deploy the Render Service\n"
        "```bash\n"
        "kubectl apply -n ${NAMESPACE} -k "
        "${REPO_ROOT}/guides/${GUIDE_NAME}/render/\n"
        "```\n",
        encoding="utf-8",
    )
    plan_config = {
        "kustomize": {
            "enabled": True,
            "guideName": "demo",
            "repoPath": str(tmp_path),
            "acceleratorBackend": "gpu/vllm",
        }
    }
    events: list[str] = []
    cmd = MagicMock()

    def kube(*args, **_kwargs):
        if args[0] == "apply":
            events.append("apply:" + args[args.index("-k") + 1])
        return FakeResult()

    cmd.kube.side_effect = kube
    cmd.wait_for_pods.side_effect = lambda **_kwargs: (
        events.append("wait") or FakeResult()
    )
    context = MagicMock()
    context.require_cmd.return_value = cmd
    context.require_namespace.return_value = "test-ns"
    context.harness_namespace = None
    context.kustomize_deploy_timeout = 30
    context.deployed_endpoints = {}

    step = KustomizeDeployStep()
    monkeypatch.setattr(step, "_load_stack_config", lambda _path: plan_config)
    monkeypatch.setattr(step, "_log_kustomize_authority", lambda *_args: None)
    monkeypatch.setattr(step, "_ensure_hf_token_secret", lambda *_args: None)
    monkeypatch.setattr(step, "_propagate_standup_parameters", lambda *_args: None)
    monkeypatch.setattr(
        _module,
        "probe_render_service",
        lambda *_args: events.append("probe") or probe_error,
    )

    result = step.execute(context, tmp_path / "stack")

    assert result.success is (probe_error is None)
    assert events == [
        f"apply:{guide_dir}/modelserver/gpu/vllm/base/",
        "wait",
        f"apply:{guide_dir}/render/",
        "probe",
    ]
    assert context.deployed_endpoints == (
        {"stack": "demo-epp"} if probe_error is None else {}
    )


def test_render_probe_rejects_missing_service():
    cmd = MagicMock()
    cmd.kube.return_value = FakeResult(success=False, stderr="NotFound")
    context = MagicMock(dry_run=False)

    error = probe_render_service(cmd, context, "demo", "test-ns")

    assert "demo-render is unavailable" in error
    cmd.kube_exec.assert_not_called()


def test_render_probe_uses_ready_service_backend_and_modelserver_container():
    service = {
        "spec": {
            "selector": {"llm-d.ai/guide": "demo", "llm-d.ai/role": "decode"},
            "ports": [{"name": "render-http", "port": 8000}],
        }
    }
    pods = {
        "items": [
            {
                "metadata": {"name": "decode-0"},
                "spec": {
                    "containers": [
                        {"name": "routing-proxy"},
                        {"name": "modelserver"},
                    ]
                },
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }
        ]
    }
    cmd = MagicMock()
    cmd.kube.side_effect = [
        FakeResult(stdout=json.dumps(service)),
        FakeResult(stdout=json.dumps(pods)),
    ]
    cmd.kube_exec.return_value = FakeResult(stdout="model=demo token_count=2")
    context = MagicMock(dry_run=False)

    assert probe_render_service(cmd, context, "demo", "test-ns") is None

    args, kwargs = cmd.kube_exec.call_args
    assert args[0:3] == ("decode-0", "python", "-c")
    assert args[4] == "http://demo-render:8000"
    assert kwargs["container"] == "modelserver"
    assert kwargs["namespace"] == "test-ns"


class _RenderHandler(BaseHTTPRequestHandler):
    token_ids: list[int] | None = [7322, 1779]
    render_status = 200

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._send_json({"data": [{"id": "Qwen/Qwen3-0.6B"}]})

    def do_POST(self):
        if self.path != "/v1/completions/render":
            self.send_error(404)
            return
        if self.render_status != 200:
            self.send_error(self.render_status)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body["model"] != "Qwen/Qwen3-0.6B":
            self.send_error(400)
            return
        self._send_json([{"token_ids": self.token_ids}])

    def _send_json(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format, *_args):
        pass


@pytest.mark.parametrize(
    "token_ids,render_status,expected_success",
    [([1, 2], 200, True), (None, 200, False), ([1, 2], 404, False)],
)
def test_render_probe_script_requires_token_ids(
    token_ids, render_status, expected_success
):
    handler = type(
        "RenderHandler",
        (_RenderHandler,),
        {"token_ids": token_ids, "render_status": render_status},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _RENDER_PROBE_SCRIPT,
                f"http://127.0.0.1:{server.server_port}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert (result.returncode == 0) is expected_success
    if expected_success:
        assert "token_count=2" in result.stdout
    elif render_status == 404:
        assert "HTTP Error 404" in result.stderr
    else:
        assert "did not return token IDs" in result.stderr
