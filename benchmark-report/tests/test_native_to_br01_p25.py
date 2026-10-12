"""Tests that the p25 latency statistics survive the vLLM benchmark import to v0.1.

The converter used to write them under a "P25" key, which the Statistics model
does not define, so every report came out with p25 unset.
"""

from pathlib import Path

from llmd_benchmark_report.native_to_br0_1 import (
    import_vllm_benchmark,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vllm_benchmark_results.json"


def test_vllm_benchmark_keeps_p25_latencies():
    latency = import_vllm_benchmark(str(FIXTURE)).metrics.latency
    assert latency.time_to_first_token.p25 == 37.4131
    assert latency.time_per_output_token.p25 == 12.6343
    assert latency.inter_token_latency.p25 == 12.4294
    assert latency.request_latency.p25 == 12976.1082
