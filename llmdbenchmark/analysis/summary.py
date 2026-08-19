"""Extract the harness stdout tail into analysis/summary.txt.

Shared by driver and pod. The bash form this replaces used ``grep -nr`` on a
single named file, which prefixes the filename, so ``cut -d: -f1`` yielded
``stdout.log`` instead of a line number and the following ``sed`` aborted --
summary.txt came out empty for every marker-based harness.

A harness with no marker (aiperf) keeps the whole log.
"""

from __future__ import annotations

from pathlib import Path

SUMMARY_MARKERS: dict[str, str] = {
    "guidellm": "Setup complete, starting benchmarks",
    "vllm-benchmark": "Result ==",
    "inferencemax": "Result ==",
}


def extract_summary(results_dir: Path, marker: str | None) -> Path | None:
    """Write ``analysis/summary.txt`` from stdout.log, starting at *marker*.

    Returns the path written, or None when there was nothing to write.
    """
    stdout_log = results_dir / "stdout.log"
    if stdout_log.is_file():
        try:
            text = stdout_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    else:
        # Logs are archived, so a driver-side call on a collected tree has to read
        # it back out. In-pod there is no archive yet and no package to import.
        try:
            from llmdbenchmark.utilities.archive import read_member
        except ImportError:
            return None
        payload = read_member(results_dir, "stdout.log")
        if payload is None:
            return None
        text = payload.decode("utf-8", errors="replace")

    lines = text.splitlines()
    if marker:
        # Last occurrence: a retried or multi-stage run logs the marker more
        # than once and only the final block is the result.
        start = None
        for idx, line in enumerate(lines):
            if marker in line:
                start = idx
        if start is None:
            return None
        lines = lines[start:]

    if not lines:
        return None

    analysis_dir = results_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    summary_path = analysis_dir / "summary.txt"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary_path
