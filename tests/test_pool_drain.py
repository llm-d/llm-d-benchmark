"""Drain check before a serving-capacity measurement (llmdbenchmark.run.pool_drain)."""

import urllib.request

from llmdbenchmark.run.pool_drain import PROBE, check, inflight


def kube_with(pods, probe_outputs):
    """kubectl stub: lists `pods`, answers each exec from probe_outputs by pod name."""
    calls = []

    def kube(argv):
        calls.append(argv)
        if argv[1:3] == ["get", "pods"]:
            return "".join(f"pod/{p}\n" for p in pods)
        pod = argv[argv.index("-n") + 2] if "-n" in argv else argv[3]
        pod = argv[argv.index("exec") + 3]
        return probe_outputs.get(pod)

    kube.calls = calls
    return kube


def test_inflight_sums_running_and_waiting_over_pods():
    kube = kube_with(["a", "b"], {"pod/a": "3", "pod/b": "4"})
    assert inflight(kube, "ns") == 7


def test_inflight_skips_unreadable_pods_but_trusts_a_reading():
    kube = kube_with(["a", "b"], {"pod/a": None, "pod/b": "2"})
    assert inflight(kube, "ns") == 2


def test_inflight_none_when_no_pod_could_be_read():
    kube = kube_with(["a"], {"pod/a": None})
    assert inflight(kube, "ns") is None


def test_check_passes_when_drained(capsys):
    kube = kube_with(["a"], {"pod/a": "0"})
    assert check(kube, "ns", timeout_s=1) == 0
    assert "drained" in capsys.readouterr().out


def test_check_times_out_while_requests_remain(capsys):
    kube = kube_with(["a"], {"pod/a": "5"})
    assert check(kube, "ns", timeout_s=0, sleep=lambda s: None) == 1
    out = capsys.readouterr().out
    assert "still in flight" in out and "restart the model pods" in out


def test_check_degrades_to_a_warning_when_metrics_unreadable(capsys):
    kube = kube_with(["a"], {"pod/a": None})
    assert check(kube, "ns", timeout_s=1) == 0
    assert "skipping the drain check" in capsys.readouterr().out


def test_the_probe_sums_running_and_waiting_from_prometheus_text(monkeypatch):
    # The one-liner that runs inside the pod, against the text vLLM serves.
    metrics = (
        "# HELP vllm:num_requests_running Number of requests running.\n"
        "# TYPE vllm:num_requests_running gauge\n"
        "vllm:num_requests_running 3\n"
        "# HELP vllm:num_requests_waiting\n"
        "# TYPE vllm:num_requests_waiting gauge\n"
        "vllm:num_requests_waiting 4\n"
        "# HELP vllm:gpu_cache_usage_perc\n"
        "# TYPE vllm:gpu_cache_usage_perc gauge\n"
        "vllm:gpu_cache_usage_perc 0.5\n"
    )

    class Resp:
        def read(self):
            return metrics.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: Resp())
    monkeypatch.setenv("VLLM_METRICS_PORT", "9999")
    ns = {}
    exec("import os,urllib.request\n" + PROBE, ns)  # noqa: S102 - the probe itself
    # urllib is already imported in this module; the exec rebinds it locally


def test_probe_is_a_single_executable_line():
    # kubectl exec carries it as one argv element: no newlines, no quotes that
    # a shell would re-parse.
    assert "\n" not in PROBE
    compile(PROBE, "<probe>", "exec")
