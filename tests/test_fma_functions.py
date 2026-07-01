"""Tests for FMA harness helpers: container-start baseline and timing_source.

Covers #599: the Kube-timestamp fallback must anchor on the requester
``inference-server`` container ``state.running.started_at`` (matching the
controller), and any revert to pod ``creation_timestamp`` must be explicit
(``timing_source == "kube_pod_create"``) and logged, never silent.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

# fma_functions.py uses bare imports (``from dpc_log_parser import ...``) that
# assume ``workload/harnesses`` is on the path, which is how it runs in the
# harness container. Mirror that here so the module imports as a plain module.
_HARNESS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "workload", "harnesses")
)
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

import fma_functions as f  # noqa: E402


def _fake_pod_with_container_start(started_at, name="inference-server"):
    """Build a minimal pod-like object with one running container status."""
    running = SimpleNamespace(started_at=started_at)
    state = SimpleNamespace(running=running)
    cs = SimpleNamespace(name=name, state=state)
    status = SimpleNamespace(container_statuses=[cs])
    return SimpleNamespace(status=status)


class TestGetContainerStartTimestamp:
    """get_container_start_timestamp mirrors the controller's #599 read."""

    def test_returns_epoch_for_running_inference_server(self):
        started = datetime(2026, 7, 1, 10, 39, 35, tzinfo=timezone.utc)
        pod = _fake_pod_with_container_start(started)
        ts = f.get_container_start_timestamp(pod)
        assert ts == started.timestamp()
        assert ts > 0.0

    def test_zero_when_container_missing(self):
        pod = _fake_pod_with_container_start(
            datetime.now(timezone.utc), name="some-sidecar"
        )
        assert f.get_container_start_timestamp(pod) == 0.0

    def test_zero_when_not_running(self):
        state = SimpleNamespace(running=None)
        cs = SimpleNamespace(name="inference-server", state=state)
        status = SimpleNamespace(container_statuses=[cs])
        pod = SimpleNamespace(status=status)
        assert f.get_container_start_timestamp(pod) == 0.0

    def test_zero_when_container_statuses_none(self):
        status = SimpleNamespace(container_statuses=None)
        pod = SimpleNamespace(status=status)
        assert f.get_container_start_timestamp(pod) == 0.0

    def test_zero_when_started_at_none(self):
        pod = _fake_pod_with_container_start(None)
        assert f.get_container_start_timestamp(pod) == 0.0


class TestSelectKubeFallbackBaseline:
    """The Kube fallback picks container-start when available, else pod-create."""

    def test_uses_container_start_when_present(self):
        ri = f.FMARequesterInfo(
            name="fma-req-1",
            creation_timestamp=1000.0,
            ready_timestamp=1005.0,
            container_start_timestamp=1002.0,
        )
        baseline, source = f.select_kube_fallback_baseline(ri)
        assert baseline == 1002.0
        assert source == "kube_container_start"

    def test_reverts_to_pod_create_and_warns(self, caplog):
        ri = f.FMARequesterInfo(
            name="fma-req-degraded",
            creation_timestamp=1000.0,
            ready_timestamp=1005.0,
            container_start_timestamp=0.0,
        )
        with caplog.at_level(logging.WARNING):
            baseline, source = f.select_kube_fallback_baseline(ri)
        assert baseline == 1000.0
        assert source == "kube_pod_create"
        # Reversion must never be silent, and must name the requester.
        assert any(
            rec.levelno == logging.WARNING and "fma-req-degraded" in rec.getMessage()
            for rec in caplog.records
        )


class TestTimingSourceField:
    """FMALauncherInfo tracks timing_source and derives dpc_timing_available."""

    def test_default_timing_source(self):
        li = f.FMALauncherInfo()
        assert li.timing_source == "kube_pod_create"
        assert li.dpc_timing_available is False

    def test_dpc_timing_available_derived_from_dpc(self):
        li = f.FMALauncherInfo(timing_source="dpc")
        assert li.dpc_timing_available is True

    def test_dpc_timing_available_false_for_kube_container_start(self):
        li = f.FMALauncherInfo(timing_source="kube_container_start")
        assert li.dpc_timing_available is False


class TestRequesterInfoContainerStartField:
    """FMARequesterInfo carries container_start_timestamp and dumps it."""

    def test_container_start_in_dump(self):
        ri = f.FMARequesterInfo(name="r", container_start_timestamp=42.0)
        dumped = ri.dump()
        assert dumped["container_start_timestamp"] == 42.0


def _fma_nop_results(timing_source, container_start_timestamp):
    """Minimal nop results dict carrying one FMA launcher_info."""
    return {
        "scenario": {
            "model": {"name": "m"},
            "deploy_methods": "fma",
            "load_format": "auto",
            "sleep_mode": "1",
            "gpus": 1,
            "platform": {
                "engines": [
                    {"name": "vllm", "version": "0.1", "args": {}, "image": "img:tag"}
                ]
            },
        },
        "time": {"duration": 1.0, "start": 0.0, "stop": 1.0},
        "vllm_metrics": [],
        "extra_metrics": [
            {
                "name": "fma",
                "iterations": [
                    {
                        "iteration": 0,
                        "hot_hit_rate": 1.0,
                        "warm_hit_rate": 0.0,
                        "cold_launcher_hit_rate": 0.0,
                        "launcher_infos": [
                            {
                                "name": "l1",
                                "requester_info": {
                                    "name": "r1",
                                    "creation_timestamp": 100.0,
                                    "ready_timestamp": 105.0,
                                    "dual_label_timestamp": 101.0,
                                    "container_start_timestamp": (
                                        container_start_timestamp
                                    ),
                                },
                                "actuation_condition": "T_hot",
                                "launcher_endpoint": "",
                                "vllm_endpoint": "",
                                "ttft": 0.5,
                                "launcher_creation_timestamp": 0.0,
                                "launcher_node": "n1",
                                "timing_source": timing_source,
                                "dpc_timing_available": timing_source == "dpc",
                                "t_wake": 3.0,
                            }
                        ],
                    }
                ],
            }
        ],
    }


class TestTimingSourceSurvivesNativeToBr01:
    """timing_source + container_start_timestamp survive native->br0.1 import."""

    def _import(self, tmp_path, results):
        import yaml
        from llmdbenchmark.analysis.benchmark_report.native_to_br0_1 import import_nop

        path = tmp_path / "results.yaml"
        path.write_text(yaml.safe_dump(results))
        br = import_nop(str(path))
        bd = br.model_dump()
        md = next(m for m in bd["metrics"]["metadata"] if m["name"] == "extra_metrics")
        return md["value"][0]["iterations"][0]["launcher_infos"][0]

    def test_kube_container_start_survives(self, tmp_path):
        li = self._import(tmp_path, _fma_nop_results("kube_container_start", 102.0))
        assert li["timing_source"] == "kube_container_start"
        assert li["requester_info"]["container_start_timestamp"]["value"] == 102.0

    def test_kube_pod_create_survives(self, tmp_path):
        li = self._import(tmp_path, _fma_nop_results("kube_pod_create", 0.0))
        assert li["timing_source"] == "kube_pod_create"

    def test_dpc_survives(self, tmp_path):
        li = self._import(tmp_path, _fma_nop_results("dpc", 102.0))
        assert li["timing_source"] == "dpc"
