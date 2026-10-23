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
        "_get_model_config": lambda *a, **kw: SimpleNamespace(),
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
    for name, fake in planner.items():
        monkeypatch.setattr(cv, name, fake)

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
