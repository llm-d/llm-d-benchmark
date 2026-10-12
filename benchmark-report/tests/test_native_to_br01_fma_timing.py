"""FMA timing fields survive the native to v0.1 conversion."""


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
        from llmd_benchmark_report.native_to_br0_1 import import_nop

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
