"""Guard against drift between the pydantic models and the committed
JSON Schema files.

The committed ``br_*_json_schema.json`` files are the language-neutral
face of the Benchmark Report format; non-Python consumers validate
against them. This test fails whenever a model change is not reflected
in the committed schema. To regenerate:

    benchmark-report -j -b <version> > llmd_benchmark_report/br_v<...>_json_schema.json
"""

import json
from pathlib import Path

import pytest

from llmd_benchmark_report import make_json_schema

PKG_DIR = Path(__file__).resolve().parent.parent / "llmd_benchmark_report"

SCHEMA_FILES = {
    "0.1": "br_v0_1_json_schema.json",
    "0.2": "br_v0_2_json_schema.json",
    "0.2.1": "br_v0_2_1_json_schema.json",
}


@pytest.mark.parametrize("version,filename", SCHEMA_FILES.items())
def test_committed_json_schema_matches_models(version: str, filename: str) -> None:
    committed = json.loads((PKG_DIR / filename).read_text())
    generated = json.loads(make_json_schema(version))
    assert generated == committed, (
        f"{filename} is stale relative to the v{version} pydantic models; "
        f"regenerate it with: benchmark-report -j -b {version}"
    )
