"""The plot modules must import with no llmdbenchmark package on sys.path.

They are copied flat into /usr/local/bin in the image, so a package-qualified
import that works on the driver would fail only in-pod, after a full benchmark.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCES = (
    "llmdbenchmark/analysis/per_request_plots.py",
    "llmdbenchmark/analysis/session_plots.py",
    "llmdbenchmark/analysis/session_metrics.py",
    "llmdbenchmark/analysis/scripts/generate_plots.py",
)


def _stage(tmp_path: Path) -> Path:
    repo = Path(__file__).resolve().parent.parent
    for source in SOURCES:
        shutil.copy2(repo / source, tmp_path / Path(source).name)
    return tmp_path


# Editing sys.path is not enough: an editable install registers a sys.meta_path
# finder at startup, so llmdbenchmark stays importable however the path is
# filtered. Blocking it at the finder is what actually reproduces the pod, where
# the package is absent entirely.
_BLOCK_PACKAGE = """
import sys

class _Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        if name == "llmdbenchmark" or name.startswith("llmdbenchmark."):
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, _Blocker())
sys.path.insert(0, ".")
"""


def test_plot_modules_import_flat(tmp_path):
    _stage(tmp_path)

    guard = subprocess.run(
        [sys.executable, "-c", _BLOCK_PACKAGE + "import llmdbenchmark"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert guard.returncode != 0, (
        "test cannot simulate the pod: llmdbenchmark leaked in"
    )

    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_PACKAGE + "import generate_plots"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# Skip, not fail: without matplotlib the plot modules no-op by design.
@pytest.mark.skipif(
    importlib.util.find_spec("matplotlib") is None, reason="needs matplotlib"
)
def test_generate_plots_writes_both_sets(tmp_path):
    _stage(tmp_path)

    results = tmp_path / "results"
    results.mkdir()
    requests = [
        {
            "start_time": 100.0 + i,
            "end_time": 100.0 + i + 0.3,
            "info": {
                "output_token_times": [100.0 + i + 0.2 + j * 0.01 for j in range(6)],
                "input_tokens": 64 + i,
                "output_tokens": 6,
            },
        }
        for i in range(10)
    ]
    import json

    (results / "per_request_lifecycle_metrics.json").write_text(json.dumps(requests))
    (
        results / "benchmark_report_v0.2,_stage_0_session_lifecycle_metrics.json.yaml"
    ).write_text(
        "results:\n"
        "  session_performance:\n"
        "    sessions:\n"
        "      session_rate: {mean: 2.0}\n"
        "      session_duration: {mean: 5.0, p50: 5.0, p99: 9.0}\n"
        "      total: 4\n"
        "      failed: 1\n"
    )

    result = subprocess.run(
        [sys.executable, "generate_plots.py", str(results)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list((results / "analysis" / "distributions").glob("*.png"))
    assert list((results / "analysis" / "session").glob("*.png"))
