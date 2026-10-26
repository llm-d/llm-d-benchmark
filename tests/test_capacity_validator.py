"""Capacity-planner diagnostics for a model whose weights do not fit.

llm-d-planner's ``allocatable_kv_cache_memory`` returns ``max(0, ...)``, so a
model too large for its GPUs comes back as 0 GB of KV cache, never a negative
number.
"""

from __future__ import annotations

import logging
import sys
import types
from types import SimpleNamespace

import pytest

# The external `planner` package is not installed in the unit-test env; every
# planner call the validator makes is replaced below.
if "planner" not in sys.modules:
    planner_stub = types.ModuleType("planner")
    capacity_stub = types.ModuleType("planner.capacity_planner")
    capacity_stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
    sys.modules["planner"] = planner_stub
    sys.modules["planner.capacity_planner"] = capacity_stub

from llmdbenchmark.utilities import capacity_validator as cv  # noqa: E402


def _validate(monkeypatch, allocatable_kv_gb: float) -> list[str]:
    planner = {
        "gpus_required": lambda tp=1, pp=1, dp=1: tp * pp * dp,
        "available_gpu_memory": lambda memory, util: memory * util,
        "get_text_config": lambda config: config,
        "find_possible_tp": lambda config: [1, 2, 4, 8],
        "max_context_len": lambda config: 32768,
        "model_total_params": lambda *a, **kw: 14_000_000_000,
        "model_memory_req": lambda *a, **kw: 27.5,
        "estimate_vllm_activation_memory": lambda *a, **kw: 1.5,
        "estimate_vllm_cuda_graph_memory": lambda: 0.0,
        "estimate_vllm_non_torch_memory": lambda tp: 0.15,
        "allocatable_kv_cache_memory": lambda *a, **kw: allocatable_kv_gb,
        "KVCacheDetail": lambda *a, **kw: SimpleNamespace(per_request_kv_cache_gb=0.75),
        "max_concurrent_requests": lambda *a, **kw: 0,
    }
    # Patched on the stub module: the validator imports the planner at call time.
    capacity_planner = sys.modules["planner.capacity_planner"]
    for name, fake in planner.items():
        monkeypatch.setattr(capacity_planner, name, fake)
    monkeypatch.setattr(cv, "_get_model_config", lambda *a, **kw: SimpleNamespace())

    params = cv.ValidationParams(
        models=["Qwen/Qwen2.5-14B-Instruct"],
        hf_token=None,
        replicas=1,
        gpu_memory=24,
        tp=1,
        pp=1,
        dp=1,
        accelerator_nr=1,
        gpu_memory_util=0.9,
        max_model_len=4096,
        label="standalone",
    )
    return cv.validate_vllm_params(params, cv._ensure_logger(logging.getLogger()))


def test_model_that_does_not_fit_is_reported_as_not_loading(monkeypatch):
    messages = _validate(monkeypatch, allocatable_kv_gb=0)

    assert any("Insufficient GPU memory to load model" in m for m in messages)
    assert not any("Model loads but cannot serve" in m for m in messages)
    assert any("27.50 GB for dp=1" in m and "21.60 GB available" in m for m in messages)


def test_model_that_loads_without_room_for_one_request(monkeypatch):
    messages = _validate(monkeypatch, allocatable_kv_gb=0.5)

    assert any("Model loads but cannot serve any requests" in m for m in messages)
    assert not any("Insufficient GPU memory" in m for m in messages)


_STANDALONE_PLAN = {"standalone": {"enabled": True, "replicas": 1}}


@pytest.mark.parametrize(("ignore", "tag"), [(True, "WARNING"), (False, "ERROR")])
def test_missing_planner_follows_ignore_failures(monkeypatch, ignore, tag):
    monkeypatch.setitem(sys.modules, "planner.capacity_planner", None)
    monkeypatch.setattr(
        cv, "_extract_params", lambda *a, **kw: pytest.fail("validation ran")
    )

    messages = cv.run_capacity_planner(
        _STANDALONE_PLAN, logging.getLogger(), ignore_failures=ignore
    )

    assert len(messages) == 1
    assert messages[0].startswith(f"{tag}: Capacity planner not available")
