"""Compatibility shim for the extracted ``llmd-benchmark-report`` package.

The Benchmark Report library now lives in ``benchmark-report/`` at the
repository root and is distributed on PyPI as ``llmd-benchmark-report``
(import name ``llmd_benchmark_report``). This shim keeps the historical
``llmdbenchmark.analysis.benchmark_report`` import path working; new code
should import ``llmd_benchmark_report`` directly.

Submodules are aliased with identity preserved, so
``from llmdbenchmark.analysis.benchmark_report.schema_v0_2 import
BenchmarkReportV02`` returns the same class object as the canonical import.
"""

import importlib
import sys

_SUBMODULES = (
    "base",
    "cli",
    "core",
    "guidellm_native",
    "metrics_processor",
    "native_to_br0_1",
    "native_to_br0_2",
    "native_to_br0_2_1",
    "schema_v0_1",
    "schema_v0_2",
    "schema_v0_2_1",
    "schema_v0_2_components",
    "timeseries",
)

_this = sys.modules[__name__]
for _sub in _SUBMODULES:
    _mod = importlib.import_module(f"llmd_benchmark_report.{_sub}")
    sys.modules[f"{__name__}.{_sub}"] = _mod
    setattr(_this, _sub, _mod)

from llmd_benchmark_report import *  # noqa: E402,F401,F403
from llmd_benchmark_report import __all__ as __all__  # noqa: E402,F401
