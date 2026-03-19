"""Analysis sub-package for post-benchmark result processing.

Provides :func:`run_analysis` which replaces the original per-harness
bash scripts (``guidellm-analyze_results.sh``, etc.) with pure-Python
equivalents that call the bundled ``benchmark_report`` library directly.

The original bash scripts are preserved under ``scripts/`` for reference.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext

logger = logging.getLogger(__name__)

# Directory containing the original analysis scripts (bash/python).
SCRIPTS_DIR: Path = Path(__file__).resolve().parent / "scripts"

# ---------------------------------------------------------------------------
# Result file patterns per harness
# ---------------------------------------------------------------------------
_RESULT_PATTERNS: dict[str, str] = {
    "inference-perf": "stage_*.json",
    "guidellm": "results.json",
    "vllm-benchmark": "openai*.json",
    "inferencemax": "*.json",
}

# Summary marker per harness — the line in stdout.log where the
# interesting output starts.
_SUMMARY_MARKERS: dict[str, str] = {
    "guidellm": "Setup complete, starting benchmarks",
    "vllm-benchmark": "Result ==",
    "inferencemax": "Result ==",
}

# Harness name → benchmark_report writer name
_WRITER_NAMES: dict[str, str] = {
    "inference-perf": "inference-perf",
    "guidellm": "guidellm",
    "vllm-benchmark": "vllm-benchmark",
    "inferencemax": "inferencemax",
    "nop": "nop",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_analysis(
    harness_name: str,
    results_dir: Path,
    context: ExecutionContext | None = None,
) -> str | None:
    """Run analysis for a single results directory.

    Calls the bundled ``benchmark_report`` library to convert raw harness
    output into standardised v0.1 / v0.2 YAML reports, then extracts a
    summary section from ``stdout.log`` (where applicable).

    For the ``nop`` harness, delegates to the original Python analysis
    script which uses the ``benchmark_report`` library directly.

    For ``inference-perf``, additionally runs ``inference-perf --analyze``
    if the binary is available on ``$PATH``.

    Returns:
        ``None`` on success, or an error string.
    """
    if harness_name == "nop":
        return _run_nop_analysis(results_dir, context)

    writer_name = _WRITER_NAMES.get(harness_name)
    if not writer_name:
        return None  # No analysis registered — not an error

    # --- 1. Convert result files to benchmark report format ---
    pattern = _RESULT_PATTERNS.get(harness_name, "*.json")
    result_files = sorted(glob.glob(str(results_dir / pattern)))

    if not result_files:
        _log(context, f"No result files matching '{pattern}' in {results_dir.name}")
        return None  # Nothing to convert — not an error

    errors: list[str] = []
    for result_file in result_files:
        result_path = Path(result_file)
        fname = result_path.name

        for br_version in ("0.1", "0.2"):
            prefix = (
                "benchmark_report" if br_version == "0.1"
                else "benchmark_report_v0.2"
            )
            output_name = f"{prefix},_{fname}.yaml"
            output_path = results_dir / output_name

            err = _convert_to_benchmark_report(
                result_path, output_path, writer_name, br_version, context,
            )
            if err:
                errors.append(err)

    # --- 2. Extract summary from stdout.log ---
    marker = _SUMMARY_MARKERS.get(harness_name)
    if marker:
        _extract_summary(results_dir, marker, context)

    # --- 3. Harness-specific post-processing ---
    if harness_name == "inference-perf":
        _run_inference_perf_analyze(results_dir, context)

    # --- 4. Generate metric plots (if metrics were collected) ---
    metrics_dir = results_dir / "metrics"
    if metrics_dir.exists():
        _run_metric_visualizations(metrics_dir, results_dir, context)

    # --- 5. Generate per-request distribution plots ---
    _run_per_request_plots(results_dir, context)

    if errors:
        return f"Conversion errors: {'; '.join(errors)}"
    return None


# ---------------------------------------------------------------------------
# Benchmark report conversion (replaces bash `benchmark-report` CLI calls)
# ---------------------------------------------------------------------------

def _convert_to_benchmark_report(
    result_file: Path,
    output_file: Path,
    writer_name: str,
    br_version: str,
    context: ExecutionContext | None,
) -> str | None:
    """Convert a single result file to benchmark report format.

    Uses the bundled ``benchmark_report`` library API when available,
    falling back to the ``benchmark-report`` CLI.
    """
    _log(context, f"Converting {result_file.name} → Benchmark Report v{br_version}")

    # Try the Python API first (faster, no subprocess)
    err = _convert_via_api(result_file, output_file, writer_name, br_version)
    if err is None:
        return None  # Success

    # Fallback to CLI
    _log(context, f"API conversion failed ({err}), trying CLI fallback...")
    return _convert_via_cli(result_file, output_file, writer_name, br_version)


def _convert_via_api(
    result_file: Path,
    output_file: Path,
    writer_name: str,
    br_version: str,
) -> str | None:
    """Attempt conversion using the benchmark_report Python API."""
    try:
        if br_version == "0.1":
            from llmdbenchmark.analysis.benchmark_report.native_to_br0_1 import (
                import_inference_perf,
                import_guidellm,
                import_vllm_benchmark,
                import_inference_max,
            )
        elif br_version == "0.2":
            from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
                import_inference_perf,
                import_guidellm,
                import_vllm_benchmark,
                import_inference_max,
            )
        else:
            return f"Unsupported BR version: {br_version}"

        converters = {
            "inference-perf": import_inference_perf,
            "guidellm": import_guidellm,
            "vllm-benchmark": import_vllm_benchmark,
            "inferencemax": import_inference_max,
        }

        convert_fn = converters.get(writer_name)
        if not convert_fn:
            return f"No API converter for writer '{writer_name}'"

        br = convert_fn(str(result_file))
        br.export_yaml(str(output_file))
        return None

    except Exception as exc:
        return str(exc)


def _convert_via_cli(
    result_file: Path,
    output_file: Path,
    writer_name: str,
    br_version: str,
) -> str | None:
    """Fallback: call the ``benchmark-report`` CLI."""
    try:
        result = subprocess.run(
            [
                "benchmark-report",
                str(result_file),
                "-b", br_version,
                "-w", writer_name,
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return (
                f"benchmark-report exited {result.returncode}: "
                f"{result.stderr[:200]}"
            )
        return None
    except FileNotFoundError:
        return "benchmark-report CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return "benchmark-report timed out (>120s)"


# ---------------------------------------------------------------------------
# Summary extraction (replaces bash grep/sed pipeline)
# ---------------------------------------------------------------------------

def _extract_summary(
    results_dir: Path,
    marker: str,
    context: ExecutionContext | None,
) -> None:
    """Extract the tail of stdout.log from *marker* into analysis/summary.txt."""
    stdout_log = results_dir / "stdout.log"
    if not stdout_log.exists():
        return

    analysis_dir = results_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    try:
        lines = stdout_log.read_text(
            encoding="utf-8", errors="replace",
        ).splitlines()
        # Find the last occurrence of the marker
        # (matches bash ``grep | tail -1``)
        start_idx = None
        for idx, line in enumerate(lines):
            if marker in line:
                start_idx = idx
        if start_idx is not None:
            summary_lines = lines[start_idx:]
            summary_path = analysis_dir / "summary.txt"
            summary_path.write_text(
                "\n".join(summary_lines) + "\n", encoding="utf-8",
            )
            _log(context, f"Summary extracted → {summary_path.name}")
    except Exception as exc:
        _log(context, f"Could not extract summary: {exc}", warning=True)


# ---------------------------------------------------------------------------
# inference-perf specific post-processing
# ---------------------------------------------------------------------------

def _run_inference_perf_analyze(
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Run ``inference-perf --analyze`` if available (matches bash script)."""
    if not shutil.which("inference-perf"):
        _log(context, "inference-perf CLI not on PATH — skipping --analyze")
        return

    analysis_dir = results_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["inference-perf", "--analyze", str(results_dir)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(results_dir),
        )
        if result.returncode != 0:
            _log(
                context,
                f"inference-perf --analyze exited {result.returncode}",
                warning=True,
            )
            return

        # Move newly created analysis files into analysis/ dir
        for item in results_dir.iterdir():
            if (
                item.is_file()
                and item.parent == results_dir
                and item.suffix in (".txt", ".csv", ".html", ".png", ".json")
            ):
                dest = analysis_dir / item.name
                if not dest.exists():
                    shutil.move(str(item), str(dest))

        _log(context, "inference-perf --analyze complete")
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        _log(context, "inference-perf --analyze timed out (>300s)", warning=True)


# ---------------------------------------------------------------------------
# Metric visualization (Prometheus time series → PNG plots)
# ---------------------------------------------------------------------------

def _run_metric_visualizations(
    metrics_dir: Path,
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Generate PNG plots for collected Prometheus metrics.

    Reads ``metrics/raw/*.txt`` files and writes PNG graphs to
    ``analysis/graphs/``.  Requires ``matplotlib`` (optional dependency).
    """
    try:
        from llmdbenchmark.analysis.visualize_metrics import (
            generate_all_visualizations,
        )
    except ImportError:
        _log(context, "matplotlib not available -- skipping metric plots")
        return

    analysis_dir = results_dir / "analysis"
    graphs_dir = analysis_dir / "graphs"
    graphs_dir.mkdir(parents=True, exist_ok=True)

    try:
        count = generate_all_visualizations(
            str(metrics_dir),
            output_dir=str(graphs_dir),
            context=context,
        )
        if count:
            _log(context, f"Generated {count} metric plot(s)")
    except Exception as exc:
        _log(context, f"Metric visualization failed: {exc}", warning=True)


# ---------------------------------------------------------------------------
# Per-request distribution plots
# ---------------------------------------------------------------------------

def _run_per_request_plots(
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Generate per-request distribution plots (histograms, CDFs, scatter).

    Reads ``per_request_lifecycle_metrics.json`` and writes plots to
    ``analysis/distributions/``.  Requires ``matplotlib``.
    """
    pr_file = results_dir / "per_request_lifecycle_metrics.json"
    if not pr_file.exists():
        return

    try:
        from llmdbenchmark.analysis.per_request_plots import (
            generate_per_request_plots,
        )
    except ImportError:
        _log(context, "matplotlib not available -- skipping per-request plots")
        return

    try:
        dist_dir = results_dir / "analysis" / "distributions"
        count = generate_per_request_plots(
            results_dir,
            output_dir=dist_dir,
            context=context,
        )
        if count:
            _log(context, f"Generated {count} per-request distribution plot(s)")
    except Exception as exc:
        _log(context, f"Per-request plot generation failed: {exc}", warning=True)


# ---------------------------------------------------------------------------
# nop harness analysis (calls the original Python script)
# ---------------------------------------------------------------------------

def _run_nop_analysis(
    results_dir: Path,
    context: ExecutionContext | None,
) -> str | None:
    """Run the nop analysis script.

    The nop analysis reads ``benchmark_report/result.yaml`` and produces
    ``analysis/result.txt``.  Currently called via subprocess because the
    script uses bare ``from benchmark_report import ...`` imports and
    ``pandas``.  A future improvement could refactor the script into an
    importable function to avoid the subprocess overhead.
    """
    script = SCRIPTS_DIR / "nop-analyze_results.py"
    if not script.exists():
        return "nop analysis script not found"

    env = os.environ.copy()
    env["LLMDBENCH_CONTROL_WORK_DIR"] = str(results_dir)

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            cwd=str(results_dir),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            detail = result.stderr[:300] or result.stdout[:300]
            return f"nop analysis failed (exit={result.returncode}): {detail}"
        _log(context, "nop analysis complete")
        return None
    except subprocess.TimeoutExpired:
        return "nop analysis timed out (>600s)"
    except Exception as exc:
        return f"nop analysis error: {exc}"


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def _log(
    context: ExecutionContext | None,
    message: str,
    warning: bool = False,
) -> None:
    """Log via context logger if available, else use module logger."""
    if context:
        if warning:
            context.logger.log_warning(message)
        else:
            context.logger.log_info(message)
    else:
        if warning:
            logger.warning(message)
        else:
            logger.info(message)
