"""Tests for VllmInfo.get_container_start (standalone container-start anchor).

Mirrors the FMA container-start baseline (get_container_start_timestamp in
fma_functions.py) but returns ELAPSED seconds (Ready - container start),
matching the shape of the existing get_pod_start().
"""

import importlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

nf = importlib.import_module("workload.harnesses.nop_functions")


def _ready_cond(ts):
    return SimpleNamespace(type="Ready", status="True", last_transition_time=ts)


def _pod(
    container_started_at, ready_at, container_name="vllm-standalone-qwen-qwen3-4b"
):
    running = SimpleNamespace(started_at=container_started_at)
    state = SimpleNamespace(running=running)
    cs = SimpleNamespace(name=container_name, state=state)
    status = SimpleNamespace(
        container_statuses=[cs],
        conditions=[_ready_cond(ready_at)],
    )
    return SimpleNamespace(status=status)


def _make_info(pod):
    v1 = MagicMock()
    v1.read_namespaced_pod.return_value = pod
    return nf.VllmStandaloneInfo(
        v1=v1,
        namespace="ns",
        pod_name="p",
        container_name="vllm-standalone-qwen-qwen3-4b",
        timeout=5.0,
    )


def test_returns_elapsed_ready_minus_container_start():
    started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    ready = datetime(2026, 1, 1, 0, 0, 42, tzinfo=timezone.utc)
    info = _make_info(_pod(started, ready))
    assert info.get_container_start() == 42.0


def test_zero_when_no_running_container():
    ready = datetime(2026, 1, 1, 0, 0, 42, tzinfo=timezone.utc)
    running = SimpleNamespace(running=None)
    cs = SimpleNamespace(name="vllm-standalone-qwen-qwen3-4b", state=running)
    status = SimpleNamespace(container_statuses=[cs], conditions=[_ready_cond(ready)])
    info = _make_info(SimpleNamespace(status=status))
    assert info.get_container_start() == 0.0


def test_falls_back_to_sole_container_when_name_mismatch():
    started = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    ready = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
    info = _make_info(_pod(started, ready, container_name="something-else"))
    assert info.get_container_start() == 10.0
