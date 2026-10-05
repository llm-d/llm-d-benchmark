"""Cross-treatment rows (and the latency-vs-load curves drawn from them) follow
stage order, not file-name order, so stage_10 comes after stage_9."""

from __future__ import annotations

import csv

import yaml

from llmdbenchmark.analysis.cross_treatment import generate_cross_treatment_summary


def test_stage_reports_are_read_in_stage_order(tmp_path):
    for treatment in (
        "inference-perf-baseline-1773947901-abc123_1",
        "inference-perf-precise-1773947901-def456_1",
    ):
        subdir = tmp_path / treatment
        subdir.mkdir()
        for stage in range(12):
            report = {
                "results": {
                    "request_performance": {
                        "aggregate": {
                            "throughput": {
                                "request_rate": {
                                    "units": "queries/s",
                                    "mean": 2.0 * (stage + 1),
                                }
                            }
                        }
                    }
                }
            }
            name = f"benchmark_report_v0.2,_stage_{stage}_lifecycle_metrics.json.yaml"
            (subdir / name).write_text(yaml.safe_dump(report))

    output_dir = tmp_path / "cmp"
    generate_cross_treatment_summary(tmp_path, output_dir)

    with open(output_dir / "treatment_comparison.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for treatment in {row["treatment"] for row in rows}:
        stages = [
            int(row["source_file"].split("_stage_")[1].split("_")[0])
            for row in rows
            if row["treatment"] == treatment
        ]
        assert stages == list(range(12))
