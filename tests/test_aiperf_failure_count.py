"""Tests that the aiperf import counts failed requests, not error types.

aiperf's ``request_count`` counts only successful requests, and its
``error_summary`` has one entry per distinct error with a ``count``.
"""

import json
from pathlib import Path

import pytest

from llmd_benchmark_report.native_to_br0_1 import import_aiperf as import_aiperf_v01
from llmd_benchmark_report.native_to_br0_2 import import_aiperf as import_aiperf_v02

# Exercises the Benchmark Report package, so CI runs it against the checkout.
pytestmark = pytest.mark.benchmark_report

FIXTURE = Path(__file__).parent / "fixtures" / "aiperf_results.json"


@pytest.fixture
def results_with_errors(tmp_path):
    """500 requests sent: 480 succeeded, 12 + 8 failed with two kinds of error."""
    results = json.loads(FIXTURE.read_text())
    results["request_count"]["avg"] = 480
    results["error_summary"] = [
        {
            "error_details": {
                "code": 503,
                "type": "Service Unavailable",
                "message": "a",
            },
            "count": 12,
        },
        {
            "error_details": {
                "code": 500,
                "type": "Internal Server Error",
                "message": "b",
            },
            "count": 8,
        },
    ]
    path = tmp_path / "profile_export_aiperf.json"
    path.write_text(json.dumps(results))
    return str(path)


def test_v02_counts_failed_requests(results_with_errors):
    requests = import_aiperf_v02(
        results_with_errors
    ).results.request_performance.aggregate.requests
    assert (requests.total, requests.failures) == (500, 20)


def test_v01_counts_failed_requests(results_with_errors):
    requests = import_aiperf_v01(results_with_errors).metrics.requests
    assert (requests.total, requests.failures) == (500, 20)
