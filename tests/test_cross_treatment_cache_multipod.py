"""The cache-vs-time overlay combines the pods of one scrape into one snapshot.

collect_metrics.sh writes one raw file per pod per scrape, named
``<pod>_<epoch>_metrics.log`` with the scrape's timestamp. Treating each file as
a deployment snapshot made consecutive samples alternate between pods, so the
KV line zig-zagged between pods and the per-interval hit rate was computed from
differences between different pods' counters.
"""

from __future__ import annotations

import pytest

from llmdbenchmark.analysis.cross_treatment import (
    _CACHE_HITS,
    _CACHE_KV,
    _CACHE_QUERIES,
    _cache_load_series,
)

# pod: (kv usage, queries at the first scrape, hits at the first scrape)
PODS = {"decode-a": (0.2, 0, 0), "decode-b": (0.6, 50_000, 20_000)}


def test_scrape_is_the_union_of_its_pod_files(tmp_path):
    raw = tmp_path / "metrics" / "raw"
    raw.mkdir(parents=True)
    for i in range(3):
        epoch = 1_773_947_901 + 5 * i
        for pod, (kv, q0, h0) in PODS.items():
            (raw / f"{pod}_{epoch}_metrics.log").write_text(
                f"# Pod: {pod}\n"
                f'vllm:kv_cache_usage_perc{{model_name="m"}} {kv}\n'
                f'vllm:prefix_cache_queries_total{{model_name="m"}} {q0 + 100 * i}\n'
                f'vllm:prefix_cache_hits_total{{model_name="m"}} {h0 + 50 * i}\n'
            )

    series = _cache_load_series(tmp_path)

    assert [t for t, _ in series] == [0.0, 5.0, 10.0]
    for i, (_, values) in enumerate(series):
        assert values[_CACHE_KV] == pytest.approx(0.4)
        assert values[_CACHE_QUERIES] == 50_000 + 200 * i
        assert values[_CACHE_HITS] == 20_000 + 100 * i
