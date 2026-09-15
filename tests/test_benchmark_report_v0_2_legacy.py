"""Reports that declare Benchmark Report version 0.2 must keep loading.

The 0.2 line has one implementation (#1922). Reports converted before its
0.2.1 revision declare ``version: "0.2"`` and already sit in results stores
and dashboards, so the current model has to read them exactly as the v0.2
model did.

``tests/fixtures/br_v0_2_legacy_example.yaml`` is the v0.2 example report as
committed before 0.2.1 became the only revision, and
``br_v0_2_legacy_example.dump.yaml`` is what the v0.2 model dumped for it.
Loading fills defaults (e.g. ``parallelism: 1``) and coerces numbers, so the
dump is compared to that recorded output rather than to the input file.

Regenerate the recorded dump only for an intentional change with:

    BR_UPDATE_GOLDEN=1 python -m pytest tests/test_benchmark_report_v0_2_legacy.py
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from llmd_benchmark_report import BenchmarkReportV02, load_benchmark_report

FIXTURES = Path(__file__).parent / "fixtures"
LEGACY_EXAMPLE = FIXTURES / "br_v0_2_legacy_example.yaml"
LEGACY_DUMP = FIXTURES / "br_v0_2_legacy_example.dump.yaml"

# Smallest document that satisfies the v0.2 required fields.
MINIMAL_V02 = {"version": "0.2", "run": {"uid": "u"}, "results": {}}


# The legacy example report, loaded through version dispatch, comes back as
# the v0.2 model with version "0.2" kept, and dumps to exactly the recorded
# br_v0_2_legacy_example.dump.yaml.
def test_legacy_example_loads_as_before() -> None:
    data = yaml.safe_load(LEGACY_EXAMPLE.read_text(encoding="utf-8"))

    report = load_benchmark_report(data)

    assert type(report) is BenchmarkReportV02
    assert report.version == "0.2"
    actual = yaml.safe_dump(report.dump(), sort_keys=True)
    if os.environ.get("BR_UPDATE_GOLDEN"):
        LEGACY_DUMP.write_text(actual, encoding="utf-8")
    assert actual == LEGACY_DUMP.read_text(encoding="utf-8")


# {"version": "0.2", "run": {"uid": "u"}, "results": {}} loads and dumps back
# unchanged, so no field a 0.2 report could omit has become required.
def test_minimal_legacy_document_round_trips() -> None:
    report = load_benchmark_report(MINIMAL_V02)

    assert type(report) is BenchmarkReportV02
    assert report.dump() == MINIMAL_V02
