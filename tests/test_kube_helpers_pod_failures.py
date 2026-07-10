"""Tests for Kubernetes pod failure reporting helpers."""

from __future__ import annotations

import json
from typing import Any

from llmdbenchmark.utilities.kube_helpers import wait_for_pods_by_label


class _Result:
    def __init__(self, success: bool = True, stdout: str = "", stderr: str = ""):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.dry_run = False


class _Command:
    def __init__(self, pods_json: str):
        self.pods_json = pods_json
        self.calls: list[tuple[str, ...]] = []

    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(args)
        if args[0] == "wait":
            return _Result()
        if args[:2] == ("get", "pods"):
            return _Result(stdout=self.pods_json)
        raise AssertionError(f"unexpected kube call: {args}")


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, *_: Any, **__: Any) -> None:
        pass

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


class _Context:
    logger = _Logger()


def test_wait_for_pods_by_label_reports_oomkilled_container() -> None:
    pods_json = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "inference-perf-abc"},
                    "status": {
                        "containerStatuses": [
                            {
                                "name": "harness",
                                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                                "lastState": {
                                    "terminated": {
                                        "reason": "OOMKilled",
                                        "exitCode": 137,
                                    }
                                },
                            }
                        ]
                    },
                }
            ]
        }
    )
    cmd = _Command(pods_json)

    errors = wait_for_pods_by_label(
        cmd,
        label="llmdbench-harness-launcher",
        namespace="bench",
        timeout=60,
        context=_Context(),
    )

    assert len(errors) == 1
    assert "inference-perf-abc/harness" in errors[0]
    assert "CrashLoopBackOff" in errors[0]
    assert "OOMKilled" in errors[0]
    assert "exit_code=137" in errors[0]
    assert ("get", "pods", "-l", "app=llmdbench-harness-launcher") == cmd.calls[2][:4]
    assert "-o" in cmd.calls[2]
    assert "json" in cmd.calls[2]
