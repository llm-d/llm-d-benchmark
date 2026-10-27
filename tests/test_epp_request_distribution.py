"""The report keeps how many requests the EPP sent to each endpoint.

process_epp_logs.py writes ``request_distribution`` as one ``{"count": n}`` per
endpoint, and br_v0_2_example.yaml shows it embedded as
``statistics: {count: n, units: count}``.
"""

from __future__ import annotations

import json
from pathlib import Path

from llmdbenchmark.analysis.benchmark_report.metrics_processor import (
    add_metrics_to_benchmark_report,
)
from workload.harnesses import process_epp_logs


def _handled(request_id: str, endpoint: str) -> str:
    entry = {
        "ts": "2026-10-01T00:00:00Z",
        "level": "info",
        "msg": "Request handled",
        "x-request-id": request_id,
        "endpoint": endpoint,
    }
    return f"[pod/epp-0/epp] {json.dumps(entry)}\n"


def test_request_distribution_keeps_each_endpoint_count(tmp_path: Path) -> None:
    metrics_dir = tmp_path / "metrics"
    log = tmp_path / "epp_pods.log"
    log.write_text(
        _handled("r1", "10.0.0.1:8000")
        + _handled("r2", "10.0.0.1:8000")
        + _handled("r3", "10.0.0.1:8000")
        + _handled("r4", "10.0.0.2:8000"),
        encoding="utf-8",
    )
    process_epp_logs.aggregate_and_output(
        process_epp_logs.parse_log_file(str(log)), str(metrics_dir), str(log)
    )

    report = add_metrics_to_benchmark_report({}, str(metrics_dir))

    components = report["results"]["observability"]["epp_request_distribution"][
        "components"
    ]
    assert {c["component_id"]: c["statistics"] for c in components} == {
        "10.0.0.1:8000": {"count": 3, "units": "count"},
        "10.0.0.2:8000": {"count": 1, "units": "count"},
    }
