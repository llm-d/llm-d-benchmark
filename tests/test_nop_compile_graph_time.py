"""The nop harness reads the torch.compile graph time from vLLM's log.

vLLM logs the shape or compile range before the elapsed seconds
("Compiling a graph for compile range %s takes %.2f s" since v0.13, "for shape
%s takes" before), so the time is the last number on the line, not the first.
"""

import importlib
from datetime import datetime

import pytest

nf = importlib.import_module("workload.harnesses.nop_functions")

PREFIX = "(EngineCore pid=271) INFO 10-01 00:00:10 [backends.py:399] "


def _parse(line: str):
    metrics = nf.BenchmarkVllmMetrics()
    logs = [
        nf.LogLine(timestamp=datetime(2026, 10, 1), line=PREFIX + line, line_number=0)
    ]
    nf.parse_logs(nf.BenchmarkScenario(), nf.PlatformEngineScenario(), metrics, logs)
    return metrics


@pytest.mark.parametrize(
    ("line", "field", "seconds"),
    [
        (
            "Compiling a graph for compile range (1, 8192) takes 12.34 s",
            "compile_graph",
            12.34,
        ),
        ("Compiling a graph for shape 512 takes 3.10 s", "compile_graph", 3.1),
        ("Compiling a graph for dynamic shape takes 12.34 s", "compile_graph", 12.34),
        (
            "Directly load the compiled graph(s) for compile range (1, 8192) from the cache, took 2.345 s",
            "load_cached_compiled_graph",
            2.345,
        ),
    ],
)
def test_graph_time_is_the_elapsed_seconds(line, field, seconds):
    assert getattr(_parse(line), field) == pytest.approx(seconds)
