"""Tests for the InferenceMAX (bench_serving) import to v0.1 and v0.2.

bench_serving writes the per-request ``input_lens`` / ``output_lens`` arrays
only with ``--save-detailed``, which the harness does not pass, so the lengths
must come from the totals it always writes. It also always writes the median
and p75 of every latency metric.
"""

import json
from pathlib import Path

import pytest

from llmd_benchmark_report.native_to_br0_1 import (
    import_inference_max as import_v01,
)
from llmd_benchmark_report.native_to_br0_2 import (
    import_inference_max as import_v02,
)

FIXTURE = Path(__file__).parent / "fixtures" / "inferencemax_results.json"
PER_REQUEST_KEYS = (
    "input_lens",
    "output_lens",
    "ttfts",
    "itls",
    "generated_texts",
    "errors",
)


def _without_save_detailed(tmp_path):
    results = json.loads(FIXTURE.read_text())
    for key in PER_REQUEST_KEYS:
        results.pop(key, None)
    path = tmp_path / "inferencemax_results.json"
    path.write_text(json.dumps(results))
    return str(path)


def _requests_and_latency(importer, path):
    report = importer(path)
    if importer is import_v01:
        return report.metrics.requests, report.metrics.latency
    aggregate = report.results.request_performance.aggregate
    return aggregate.requests, aggregate.latency


@pytest.mark.parametrize("importer", [import_v01, import_v02], ids=["v0.1", "v0.2"])
def test_lengths_come_from_totals_without_save_detailed(importer, tmp_path):
    requests, _ = _requests_and_latency(importer, _without_save_detailed(tmp_path))
    # total_input_tokens 290357 and total_output_tokens 28771 over 32 completed
    assert requests.input_length.mean == pytest.approx(9073.65625)
    assert requests.output_length.mean == pytest.approx(899.09375)


@pytest.mark.parametrize("importer", [import_v01, import_v02], ids=["v0.1", "v0.2"])
def test_itl_and_e2el_keep_p50_and_p75(importer):
    _, latency = _requests_and_latency(importer, str(FIXTURE))
    assert latency.inter_token_latency.p50 == 24.3411
    assert latency.inter_token_latency.p75 == 26.2342
    assert latency.request_latency.p50 == 23145.1596
    assert latency.request_latency.p75 == 23983.6874
