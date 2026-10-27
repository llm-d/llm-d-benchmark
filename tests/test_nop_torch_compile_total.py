"""The nop harness finds the torch.compile total and the API server start in
the log lines current vLLM writes.

vLLM 0.17.1 replaced "torch.compile takes %.2f s in total" with "torch.compile
took ..." (or "torch.compile and initial profiling/warmup run together took
..."), optionally prefixed with "[tag] ", and logs "Starting vLLM server on %s"
where it once logged "Starting vLLM API server".
"""

import importlib
from datetime import datetime

import pytest

nf = importlib.import_module("workload.harnesses.nop_functions")

ENGINE = "(EngineCore pid=271) INFO 10-01 00:00:{:02d} [x.py:1] "
API = "(APIServer pid=1) INFO 10-01 00:00:{:02d} [x.py:1] "


@pytest.mark.parametrize(
    "line",
    [
        "torch.compile takes 17.88 s in total",
        "torch.compile took 17.88 s in total",
        "[backbone] torch.compile took 17.88 s in total",
        "torch.compile and initial profiling/warmup run together took 17.88 s in total",
    ],
)
def test_torch_compile_total(line):
    metrics = nf.BenchmarkVllmMetrics()
    logs = [
        nf.LogLine(
            timestamp=datetime(2026, 10, 1), line=ENGINE.format(0) + line, line_number=0
        )
    ]
    nf.parse_logs(nf.BenchmarkScenario(), nf.PlatformEngineScenario(), metrics, logs)
    assert metrics.torch_compile == pytest.approx(17.88)


def test_compile_and_server_categories_close():
    logs = nf.get_log_list(
        "\n".join(
            [
                ENGINE.format(10) + "Start compiling function <code object forward>",
                ENGINE.format(14) + "Dynamo bytecode transform time: 3.96 s",
                ENGINE.format(30) + "torch.compile took 17.88 s in total",
                ENGINE.format(50) + "init engine (profile, create kv cache, "
                "warmup model) took 40.00 seconds",
                API.format(52) + "Starting vLLM server on http://0.0.0.0:8000",
                API.format(53) + "Route: /metrics, Methods: GET",
            ]
        )
    )
    root = nf.categorize_logs(nf.get_log_list_per_process("model", logs))
    categories = {c["title"]: c for c in root.dump()}

    compilation = categories["Pytorch Compilation"]
    assert compilation["elapsed"] == 20.0
    assert [c["elapsed"] for c in compilation["categories"]] == [4.0, 16.0]
    assert categories["CUDA Graph Capture"]["elapsed"] == 20.0
    assert categories["API Server Starts"]["elapsed"] == 1.0
