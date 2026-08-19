"""Summary extraction: marker selection, no-marker passthrough, flat in-pod import."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from llmdbenchmark.analysis.summary import extract_summary

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "llmdbenchmark" / "analysis"

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


def test_extracts_from_last_marker(tmp_path):
    (tmp_path / "stdout.log").write_text(
        "noise\nResult ==\nstale\nResult ==\nkeep me\n", encoding="utf-8"
    )

    written = extract_summary(tmp_path, "Result ==")

    assert written == tmp_path / "analysis" / "summary.txt"
    # A retried run logs the marker twice; only the final block is the result.
    assert written.read_text(encoding="utf-8") == "Result ==\nkeep me\n"


def test_no_marker_keeps_whole_log(tmp_path):
    (tmp_path / "stdout.log").write_text("line one\nline two\n", encoding="utf-8")

    written = extract_summary(tmp_path, None)

    assert written.read_text(encoding="utf-8") == "line one\nline two\n"


def test_missing_marker_and_missing_log(tmp_path):
    (tmp_path / "stdout.log").write_text("nothing of interest\n", encoding="utf-8")
    assert extract_summary(tmp_path, "Result ==") is None
    assert not (tmp_path / "analysis").exists()

    assert extract_summary(tmp_path / "absent", "Result ==") is None


def test_entry_point_imports_flat(tmp_path):
    """The pod ships summary.py next to the script, with no llmdbenchmark package."""
    results = tmp_path / "results"
    results.mkdir()
    (results / "stdout.log").write_text("noise\nResult ==\ntail\n", encoding="utf-8")

    guard = subprocess.run(
        [sys.executable, "-c", _BLOCK_PACKAGE + "import llmdbenchmark"],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS_DIR),
    )
    assert guard.returncode != 0, (
        "test cannot simulate the pod: llmdbenchmark leaked in"
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            _BLOCK_PACKAGE
            + "sys.argv = ['extract_summary.py', sys.argv[1], 'Result ==']\n"
            + "exec(open('scripts/extract_summary.py').read())",
            str(results),
        ],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS_DIR),
    )

    assert proc.returncode == 0, proc.stderr
    assert (results / "analysis" / "summary.txt").read_text(
        encoding="utf-8"
    ) == "Result ==\ntail\n"
