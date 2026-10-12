"""The vLLM benchmark import to v0.2 averages input length over completed requests.

vLLM's ``total_input_tokens`` sums prompt lengths over successful requests only,
so dividing it by ``num_prompts`` understated the mean whenever requests failed.
The v0.1 converter and v0.2's output length already divide by ``completed``.
"""

from pathlib import Path

import pytest

from llmd_benchmark_report.native_to_br0_1 import (
    import_vllm_benchmark as import_v01,
)
from llmd_benchmark_report.native_to_br0_2 import (
    import_vllm_benchmark as import_v02,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vllm_benchmark_results.json"


def test_input_length_is_averaged_over_completed_requests():
    # The fixture has 198 completed of 200 prompts and 46786 input tokens.
    requests = import_v02(str(FIXTURE)).results.request_performance.aggregate.requests
    assert requests.input_length.mean == pytest.approx(46786 / 198)
    assert requests.input_length.mean == pytest.approx(
        import_v01(str(FIXTURE)).metrics.requests.input_length.mean
    )
