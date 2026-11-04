"""Tests for FMA harness helpers: container-start baseline and timing_source.

The Kube-timestamp fallback must anchor on the requester ``inference-server``
container ``state.running.started_at`` (matching the dual-pods controller's
actuation baseline), and any revert to pod ``creation_timestamp`` must be
explicit (``timing_source == "kube_pod_create"``) and logged, never silent.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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
    """get_container_start_timestamp mirrors the controller's actuation-baseline read."""

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

    def test_reverts_to_pod_create_selection(self, caplog):
        ri = f.FMARequesterInfo(
            name="fma-req-degraded",
            creation_timestamp=1000.0,
            ready_timestamp=1005.0,
            container_start_timestamp=0.0,
        )
        # Selection is pure: it picks the pod-create baseline but does NOT warn,
        # because DPC refinement may still override the source to "dpc" later.
        with caplog.at_level(logging.WARNING):
            baseline, source = f.select_kube_fallback_baseline(ri)
        assert baseline == 1000.0
        assert source == "kube_pod_create"
        assert not any(rec.levelno == logging.WARNING for rec in caplog.records)


class TestWarnOnPodCreateBaseline:
    """The deferred warning names the requester and is emitted at WARNING level."""

    def test_warns_and_names_requester(self, caplog):
        with caplog.at_level(logging.WARNING):
            f.warn_on_pod_create_baseline("fma-req-degraded")
        assert any(
            rec.levelno == logging.WARNING and "fma-req-degraded" in rec.getMessage()
            for rec in caplog.records
        )


def _emit_deferred_pod_create_warnings(launcher_infos):
    """Mirror the production deferred-warning pass over final timing_source."""
    for li in launcher_infos:
        if li.timing_source == "kube_pod_create":
            f.warn_on_pod_create_baseline(li.requester_info.name)


class TestDeferredWarningRespectsFinalSource:
    """Warning fires only for FINAL kube_pod_create, not DPC-overridden ones."""

    def test_dpc_override_suppresses_warning(self, caplog):
        # Requester started with container_start unavailable (tentative
        # kube_pod_create) but DPC refinement overrode timing_source to "dpc".
        refined = f.FMALauncherInfo(
            requester_info=f.FMARequesterInfo(
                name="fma-req-refined", creation_timestamp=1000.0
            ),
            timing_source="dpc",
        )
        with caplog.at_level(logging.WARNING):
            _emit_deferred_pod_create_warnings([refined])
        # No spurious "may be overstated" warning for a DPC-timed requester.
        assert not any("fma-req-refined" in rec.getMessage() for rec in caplog.records)

    def test_genuine_pod_create_still_warns(self, caplog):
        degraded = f.FMALauncherInfo(
            requester_info=f.FMARequesterInfo(
                name="fma-req-degraded", creation_timestamp=1000.0
            ),
            timing_source="kube_pod_create",
        )
        with caplog.at_level(logging.WARNING):
            _emit_deferred_pod_create_warnings([degraded])
        assert any(
            rec.levelno == logging.WARNING and "fma-req-degraded" in rec.getMessage()
            for rec in caplog.records
        )

    def test_container_start_source_does_not_warn(self, caplog):
        cs = f.FMALauncherInfo(
            requester_info=f.FMARequesterInfo(name="fma-req-cs"),
            timing_source="kube_container_start",
        )
        with caplog.at_level(logging.WARNING):
            _emit_deferred_pod_create_warnings([cs])
        assert not any("fma-req-cs" in rec.getMessage() for rec in caplog.records)


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


class TestGetFmaLauncherInfos:
    """vLLM endpoint discovery: the requester's proxy config is
    authoritative; the ISC's modelServerConfig.port only serves legacy
    ISCs that still carry one (rendered configs have none), and a
    requester with neither is skipped."""

    def _requester(self):
        pod = SimpleNamespace(
            metadata=SimpleNamespace(
                name="req-1",
                labels={f.DUAL_LABEL: "launcher-1"},
                annotations={
                    "dual-pods.llm-d.ai/inference-server-config": "isc-1",
                    f.ADMIN_PORT_ANNOTATION: "8081",
                },
            ),
            status=SimpleNamespace(pod_ip="10.0.0.9"),
        )
        return SimpleNamespace(pod=pod)

    def _launcher_pod(self):
        return SimpleNamespace(
            metadata=SimpleNamespace(
                name="launcher-1",
                creation_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                namespace="ns",
            ),
            spec=SimpleNamespace(
                node_name="node-a",
                containers=[SimpleNamespace(name="inference-server", image="img:1")],
            ),
            status=SimpleNamespace(pod_ip="10.0.0.5"),
        )

    def _run(self, monkeypatch, proxy_response, isc):
        """get_fma_launcher_infos with a mocked proxy GET + kube APIs."""
        monkeypatch.setattr(f.requests, "get", lambda url, timeout=None: proxy_response)
        core = MagicMock()
        core.read_namespaced_pod.return_value = self._launcher_pod()
        monkeypatch.setattr(f.client, "CoreV1Api", lambda: core)
        api = MagicMock()
        api.get_namespaced_custom_object.return_value = isc
        infos = f.get_fma_launcher_infos(
            MagicMock(), api, [self._requester()], "ns", "8001", f.BenchmarkResult()
        )
        return infos, api

    def test_proxy_config_supplies_endpoint(self, monkeypatch):
        resp = SimpleNamespace(
            status_code=200, json=lambda: {"address": "10.0.0.5", "port": 8002}
        )
        infos, api = self._run(monkeypatch, resp, isc={"spec": {}})
        assert [i.vllm_endpoint for i in infos] == ["http://10.0.0.5:8002"]
        api.get_namespaced_custom_object.assert_not_called()

    def test_falls_back_to_isc_port_when_proxy_unconfigured(self, monkeypatch):
        infos, _ = self._run(
            monkeypatch,
            SimpleNamespace(status_code=404),
            isc={"spec": {"modelServerConfig": {"port": 8000}}},
        )
        assert [i.vllm_endpoint for i in infos] == ["http://10.0.0.5:8000"]

    def test_raises_when_no_requester_yields_a_port(self, monkeypatch):
        """Every requester skipped (proxy GET failing everywhere) must raise,
        not silently produce an empty benchmark."""
        with pytest.raises(RuntimeError, match="no launcher info collected"):
            self._run(monkeypatch, SimpleNamespace(status_code=404), isc={"spec": {}})

    def test_empty_requesters_returns_empty_without_raising(self, monkeypatch):
        """Scale-to-zero: no requesters is legitimate, not an error."""
        monkeypatch.setattr(
            f.requests,
            "get",
            lambda url, timeout=None: SimpleNamespace(status_code=404),
        )
        assert (
            f.get_fma_launcher_infos(
                MagicMock(), MagicMock(), [], "ns", "8001", f.BenchmarkResult()
            )
            == []
        )
