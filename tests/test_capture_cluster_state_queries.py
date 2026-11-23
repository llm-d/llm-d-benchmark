"""Step 09a's Thanos diagnostic queries (run/steps/step_09a_capture_cluster_state.py).

Behavior under test:
- the vLLM cache queries read ``vllm:kv_cache_usage_perc``, the KV cache gauge
  vLLM exposes. vLLM 0.12 and later do not expose ``vllm:gpu_cache_usage_perc``,
  and a query for a metric nobody exposes succeeds with no series, which reads
  as "the series carry no ``llm_d_ai_variant`` label".
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import yaml

from llmdbenchmark.run.steps import step_09a_capture_cluster_state as step


@dataclass
class _Result:
    success: bool
    stdout: str
    stderr: str = ""


def _instant_queries(tmp_path, monkeypatch) -> list[str]:
    """Run the step against a fake cluster; return the PromQL it sent to Thanos."""
    monkeypatch.setattr(step, "write_sizing", lambda *args, **kwargs: [])
    stack = tmp_path / "stack"
    stack.mkdir()
    (stack / "config.yaml").write_text(
        yaml.safe_dump({"eppKedaSaturation": {"enabled": True}})
    )

    def kube(*args, check=False):
        if args[:2] == ("get", "route"):
            return _Result(True, "thanos.example")
        if args[:2] == ("whoami", "-t"):
            return _Result(True, "token")
        return _Result(False, "")

    queries = []

    def execute(command, check=False, stdin=None):
        url = urlsplit(command.split('"')[1])
        if url.path == "/api/v1/query":
            queries.append(parse_qs(url.query)["query"][0])
        return _Result(True, '{"status": "success"}')

    context = SimpleNamespace(
        namespace="bench",
        experiment_ids=["exp-1"],
        run_results_dir=lambda: tmp_path / "results",
        require_cmd=lambda: SimpleNamespace(kube=kube, execute=execute),
        logger=SimpleNamespace(
            log_info=lambda *a, **k: None, log_warning=lambda *a, **k: None
        ),
    )

    step.CaptureClusterStateStep().execute(context, stack)
    return queries


def test_vllm_cache_queries_read_the_gauge_vllm_exposes(tmp_path, monkeypatch):
    queries = _instant_queries(tmp_path, monkeypatch)

    assert "vllm:kv_cache_usage_perc" in queries
    assert 'vllm:kv_cache_usage_perc{llm_d_ai_variant=~".+"}' in queries
    assert 'vllm:kv_cache_usage_perc{llm_d_ai_variant=""}' in queries
    assert not [q for q in queries if "gpu_cache_usage_perc" in q]
    assert (tmp_path / "results" / "exp-1" / "thanos-vllm-cache-all.json").exists()
