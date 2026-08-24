"""Tests for the priority-mix harness."""

from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any


_HARNESS_PATH = (
    Path(__file__).resolve().parent.parent
    / "workload"
    / "harnesses"
    / "priority_mix.py"
)
_spec = importlib.util.spec_from_file_location("priority_mix", _HARNESS_PATH)
priority_mix = importlib.util.module_from_spec(_spec)
sys.modules["priority_mix"] = priority_mix
_spec.loader.exec_module(priority_mix)


def test_traffic_classes_add_objective_and_fairness_headers() -> None:
    classes = priority_mix.traffic_classes(
        {
            "trafficClasses": [
                {
                    "name": "critical",
                    "weight": 1,
                    "objective": "app-critical",
                    "fairnessID": "tenant-a",
                    "priority": 100,
                }
            ]
        }
    )

    assert classes[0].headers == {
        priority_mix.OBJECTIVE_HEADER: "app-critical",
        priority_mix.FAIRNESS_HEADER: "tenant-a",
    }
    assert classes[0].priority == 100


def test_traffic_class_request_overrides_prompt_template() -> None:
    profile = {
        "model": "test-model",
        "request": {"prompt": "global", "max_tokens": 8},
        "trafficClasses": [
            {
                "name": "critical",
                "weight": 1,
                "objective": "app-critical",
                "request": {
                    "promptTemplate": "critical prefix {traffic_class} {variation}. ",
                    "promptRepeat": 2,
                },
            }
        ],
    }

    traffic_class = priority_mix.traffic_classes(profile)[0]
    payload = priority_mix.build_payload(profile, traffic_class, 7)

    assert payload["messages"][0]["content"] == (
        "critical prefix critical 7. critical prefix critical 7. "
    )
    assert payload["max_tokens"] == 8


def test_priority_mix_sends_each_objective_header() -> None:
    seen: list[str] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            with lock:
                seen.append(self.headers.get(priority_mix.OBJECTIVE_HEADER, ""))
            body = json.dumps({"id": "ok", "choices": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = {
            "endpoint_url": f"http://127.0.0.1:{server.server_port}",
            "model": "test-model",
            "load": {
                "duration_seconds": 1,
                "rate_per_second": 100,
                "max_in_flight": 4,
                "total_requests": 4,
            },
            "trafficClasses": [
                {"name": "critical", "weight": 1, "objective": "app-critical"},
                {"name": "normal", "weight": 1, "objective": "app-normal"},
            ],
        }
        output = priority_mix.run(profile, priority_mix.logging.getLogger("test"))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert output["summary"]["total_requests"] == 4
    assert output["summary"]["errors"] == 0
    assert seen.count("app-critical") == 2
    assert seen.count("app-normal") == 2


def test_send_request_sets_error_not_ttft_on_connection_failure() -> None:
    traffic_class = priority_mix.TrafficClass(name="critical", weight=1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]

    result = priority_mix.send_request(
        f"http://127.0.0.1:{unused_port}/v1/chat/completions",
        {"model": "test-model", "messages": [], "stream": False},
        traffic_class,
        timeout_seconds=2,
    )

    assert result.status_code == 0
    assert result.error is not None
    assert result.ttft_ms is None

    summary = priority_mix.summarize([result], [traffic_class])
    assert summary["successes"] == 0
    assert summary["errors"] == 1


def test_priority_mix_reports_streaming_ttft_and_tpot() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for token in ("hello", " ", "world"):
                time.sleep(0.01)
                chunk = {"choices": [{"delta": {"content": token}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = {
            "endpoint_url": f"http://127.0.0.1:{server.server_port}",
            "model": "test-model",
            "load": {
                "duration_seconds": 1,
                "rate_per_second": 100,
                "max_in_flight": 1,
                "total_requests": 1,
            },
            "request": {"stream": True},
            "trafficClasses": [
                {"name": "critical", "weight": 1, "objective": "app-critical"},
            ],
        }
        output = priority_mix.run(profile, priority_mix.logging.getLogger("test"))
    finally:
        server.shutdown()
        thread.join(timeout=5)

    summary = output["summary"]["traffic_classes"]["critical"]
    assert summary["requests"] == 1
    assert summary["ttft_ms"]["avg"] > 0
    assert summary["tpot_ms"]["avg"] > 0
    assert summary["output_tokens"] == 3
