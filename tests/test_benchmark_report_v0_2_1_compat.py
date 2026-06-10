"""Backward-compatibility guarantee for benchmark report schema v0.2.1.

v0.2.1 is a strict additive superset of v0.2: every document that is valid
under v0.2 MUST also be valid under v0.2.1. This is a hard requirement, so it
is enforced here rather than relying on manual inspection.

The proof has three independent angles:
  1. Concrete: the committed v0.2 example validates unchanged under v0.2.1.
  2. Semantic: v0.2 data dumps identically whether parsed as v0.2 or v0.2.1
     (v0.2.1 neither drops, renames, nor injects fields for v0.2 input).
  3. Structural: every field v0.2.1 adds is Optional, so no previously valid
     document can become invalid for want of a newly required field.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import (
    AggregateRequests as AggregateRequestsV02,
    AggregateThroughput as AggregateThroughputV02,
    BenchmarkReportV02,
)
from llmdbenchmark.analysis.benchmark_report.schema_v0_2_1 import (
    AggregateRequests as AggregateRequestsV021,
    AggregateThroughput as AggregateThroughputV021,
    BenchmarkReportV021,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BR_DIR = PROJECT_ROOT / "llmdbenchmark" / "analysis" / "benchmark_report"
V02_EXAMPLE = BR_DIR / "br_v0_2_example.yaml"

# Smallest document that satisfies the v0.2 required fields.
MINIMAL_V02 = {"version": "0.2", "run": {"uid": "u"}, "results": {}}


def _load(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# --- 1. Concrete: real v0.2 documents validate under v0.2.1 -------------------


@pytest.mark.parametrize(
    "data", [_load(V02_EXAMPLE), MINIMAL_V02], ids=["example", "minimal"]
)
def test_v0_2_document_validates_under_both_versions(data):
    # Sanity: it is genuinely a valid v0.2 document...
    BenchmarkReportV02(**data)
    # ...and therefore must also be a valid v0.2.1 document.
    BenchmarkReportV021(**data)


# --- 2. Semantic: v0.2.1 is a no-op for v0.2 data -----------------------------


def test_v0_2_data_dumps_identically_under_v0_2_1():
    data = _load(V02_EXAMPLE)
    assert BenchmarkReportV021(**data).dump() == BenchmarkReportV02(**data).dump()


# --- 3. Structural: every field v0.2.1 adds is Optional ------------------------


@pytest.mark.parametrize(
    "v021_model, v02_model",
    [
        (AggregateRequestsV021, AggregateRequestsV02),
        (AggregateThroughputV021, AggregateThroughputV02),
        (BenchmarkReportV021, BenchmarkReportV02),
    ],
)
def test_added_fields_are_optional(v021_model, v02_model):
    added = set(v021_model.model_fields) - set(v02_model.model_fields)
    required = [n for n in added if v021_model.model_fields[n].is_required()]
    assert not required, f"{v021_model.__name__} adds required field(s): {required}"


def test_no_v0_2_field_becomes_required_in_v0_2_1():
    # Any field shared with v0.2 must not have gained a required constraint.
    for v021_model, v02_model in [
        (AggregateRequestsV021, AggregateRequestsV02),
        (AggregateThroughputV021, AggregateThroughputV02),
        (BenchmarkReportV021, BenchmarkReportV02),
    ]:
        for name, field in v02_model.model_fields.items():
            if not field.is_required():
                assert not v021_model.model_fields[name].is_required(), (
                    f"{v021_model.__name__}.{name} became required in v0.2.1"
                )
