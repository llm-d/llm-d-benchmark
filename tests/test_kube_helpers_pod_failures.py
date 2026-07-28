"""Tests for informative harness pod failure reporting."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from llmdbenchmark.utilities.kube_helpers import wait_for_pods_by_label


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass


class _Result:
    def __init__(
        self, *, success: bool = True, stdout: str = "", stderr: str = ""
    ) -> None:
        self.success = success
        self.stdout = stdout
        self.stderr = stderr


class _Command:
    def __init__(self, pod_list: dict) -> None:
        self.pod_list = pod_list
        self.calls: list[tuple[str, ...]] = []

    def kube(self, *args: str, **_: Any) -> _Result:
        self.calls.append(args)
        if args[:2] == ("get", "pods"):
            if "jsonpath={.items[*].status.phase}" in args:
                return _Result(stdout="Failed")
            return _Result(stdout=json.dumps(self.pod_list))
        return _Result()


def test_wait_reports_current_and_previous_container_failure() -> None:
    pod_list = {
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
    cmd = _Command(pod_list)
    context = SimpleNamespace(logger=_Logger())

    errors = wait_for_pods_by_label(
        cmd,
        "llmdbench-harness-launcher",
        "bench",
        3600,
        context,
    )

    assert errors == [
        "Found pods in error state: inference-perf-abc/harness "
        "(CrashLoopBackOff, last terminated: OOMKilled, exit_code=137)"
    ]
    get_call = next(
        call
        for call in cmd.calls
        if call[:2] == ("get", "pods") and call[-2:] == ("-o", "json")
    )
    assert get_call[-2:] == ("-o", "json")
