"""Tests for the T_actuation_cstart column in write_fma_metrics.

write_fma_metrics emits the per-iteration actuation table. Alongside the
existing T_actuation column (ready - creation_timestamp, the pod-create
anchor), it must emit a T_actuation_cstart column measured from the
requester container's start (ready - container_start_timestamp), so the
container-start anchor is visible next to the pod-create anchor. The column
must degrade gracefully on older artifacts that lack container_start_timestamp.
"""

import importlib.util
import io
import os
import sys

_ANALYSIS = os.path.join("llmdbenchmark", "analysis")
_SCRIPTS = os.path.join(_ANALYSIS, "scripts")
for _p in (_ANALYSIS, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_SPEC = importlib.util.spec_from_file_location(
    "nop_analyze_results",
    os.path.join(_SCRIPTS, "nop-analyze_results.py"),
)
nar = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nar)


def _scalar(value):
    return {"value": value}


def _iteration(creation, ready, container_start=None):
    requester_info = {
        "creation_timestamp": _scalar(creation),
        "ready_timestamp": _scalar(ready),
        "gpu_uuids": "",
    }
    if container_start is not None:
        requester_info["container_start_timestamp"] = _scalar(container_start)
    return {
        "iteration": _scalar(1),
        "launcher_infos": [
            {
                "name": "vllm-qwen3-4b",
                "launcher_node": "node-a",
                "requester_info": requester_info,
                "ttft": _scalar(0.5),
                "actuation_condition": "T_hot",
                "timing_source": "dpc",
            }
        ],
    }


def test_writes_t_actuation_cstart_column_header():
    out = io.StringIO()
    # ready=130, creation=100 -> T_actuation 30; container_start=112 -> cstart 18
    nar.write_fma_metrics(out, [_iteration(100.0, 130.0, 112.0)], 0)
    text = out.getvalue()
    assert "T_actuation_cstart(s)" in text


def test_computes_cstart_from_container_start_timestamp():
    out = io.StringIO()
    nar.write_fma_metrics(out, [_iteration(100.0, 130.0, 112.0)], 0)
    text = out.getvalue()
    # container-start anchored actuation = 130 - 112 = 18.0
    assert "18.0000" in text


def test_still_writes_t_actuation_column():
    out = io.StringIO()
    nar.write_fma_metrics(out, [_iteration(100.0, 130.0, 112.0)], 0)
    text = out.getvalue()
    assert "T_actuation(s)" in text
    # pod-create anchored actuation = 130 - 100 = 30.0
    assert "30.0000" in text


def test_cstart_absent_container_start_falls_back_gracefully():
    out = io.StringIO()
    # Old artifact: no container_start_timestamp field at all.
    nar.write_fma_metrics(out, [_iteration(100.0, 130.0, None)], 0)
    text = out.getvalue()
    # Column still present; the pod-create anchor is unaffected.
    assert "T_actuation_cstart(s)" in text
    assert "30.0000" in text
