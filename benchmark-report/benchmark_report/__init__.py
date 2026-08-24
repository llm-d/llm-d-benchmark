"""Deprecated alias for :mod:`llmd_benchmark_report`.

The harness pod scripts historically import the Benchmark Report library
under the bare top-level name ``benchmark_report``. The canonical import
name is now ``llmd_benchmark_report``; this alias keeps existing imports
working and will be removed in a future release.

Submodules are aliased with identity preserved, so
``from benchmark_report.schema_v0_2 import BenchmarkReportV02`` returns
the same class object as the canonical import.
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
