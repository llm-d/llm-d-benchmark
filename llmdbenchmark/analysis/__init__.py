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

from llmdbenchmark.analysis.summary import SUMMARY_MARKERS, extract_summary

from llmdbenchmark.analysis.metrics_embed import (  # noqa: F401
    REPORT_STAGE_RE as _REPORT_STAGE_RE,
    stage_windows as _stage_windows,
)
from llmdbenchmark.utilities.archive import read_member

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
    "eval-containers": "task/result.json",
}

# Summary marker per harness -- the line in stdout.log where the
# interesting output starts. Shared with the in-pod extractor.
_SUMMARY_MARKERS = SUMMARY_MARKERS

# Every harness analyses in the pod at exit. Re-running on the driver only rewrites
# identical artifacts and forces a collected set to be read back out of its archive.
# The driver pass stays the fallback for a set the pod did not analyse.
_IN_POD_ANALYZERS = frozenset(
    {
        "inference-perf",
        "guidellm",
        "vllm-benchmark",
        "inferencemax",
        "aiperf",
        "nop",
        "lm-eval",
        "eval-containers",
    }
)


def pod_analysis_present(harness_name: str, results_dir: Path) -> bool:
    """True when *results_dir* already holds the pod's own analysis output.

    Presence is decided on the artifacts, not on the harness name alone: an
    older image, a failed analyzer, or a hand-assembled directory leaves the
    reports missing, and re-running on the driver is the fallback for exactly
    those cases.
    """
    if harness_name not in _IN_POD_ANALYZERS:
        return False
    if harness_name == "nop":
        # Archived by default, and sync_analysis_dir moves analysis/ off the tree
        # during collection either way -- so a plain check reads as "the pod did
        # nothing" and hands the work to a driver path whose own input is archived
        # too, failing the run.
        return read_member(results_dir, "analysis/result.txt") is not None
    return any(results_dir.glob("benchmark_report_v0.2,_*.yaml"))


# Harness name to benchmark_report writer name
_WRITER_NAMES: dict[str, str] = {
    "inference-perf": "inference-perf",
    "guidellm": "guidellm",
    "vllm-benchmark": "vllm-benchmark",
    "inferencemax": "inferencemax",
    "nop": "nop",
    "eval-containers": "eval-containers",
}


def _reset_harness_meta_cache() -> None:
    """Drop the memoized run_metadata.yaml; one process analyses many subdirs."""
    from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
        _get_harness_meta,
    )

    if hasattr(_get_harness_meta, "_cache"):
        del _get_harness_meta._cache


def _recorded_for(results_dir: Path, key: str) -> str:
    """Read one of this directory's own metadata values, ignoring the ambient envar.

    Args:
        results_dir (Path): directory being converted.
        key (str): run_metadata.yaml key to read.

    Returns:
        str: the recorded value, or "" for a run that predates it.
    """
    import yaml

    try:
        with (results_dir / "run_metadata.yaml").open(encoding="utf-8") as meta_file:
            metadata = yaml.safe_load(meta_file) or {}
    except (OSError, yaml.YAMLError):
        return ""
    return str(metadata.get(key) or "").strip()


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
    # Returns None (success) so the caller still counts this result set: the
    # cross-treatment comparison is gated on that count and is driver-only work.
    if pod_analysis_present(harness_name, results_dir):
        _log(context, f"{results_dir.name}: using in-pod analysis")
        return None

    if harness_name == "nop":
        return _run_nop_analysis(results_dir, context)

    writer_name = _WRITER_NAMES.get(harness_name)
    if not writer_name:
        return None  # No analysis registered -- not an error

    # --- 1. Convert result files to benchmark report format ---
    pattern = _RESULT_PATTERNS.get(harness_name, "*.json")
    result_files = sorted(glob.glob(str(results_dir / pattern)))

    # A fixed-path input may be archived rather than absent. The converters take a
    # path and resolve the result root from it, reading through read_member, so the
    # path only has to name the member -- it does not have to exist on disk.
    if not result_files and not glob.has_magic(pattern):
        if read_member(results_dir, pattern) is not None:
            result_files = [str(results_dir / pattern)]

    if not result_files:
        _log(context, f"No result files matching '{pattern}' in {results_dir.name}")
        return None  # Nothing to convert -- not an error

    # The converters resolve run identity relative to these envars. The harness
    # pod sets them, the driver does not, and these reports overwrite the in-pod
    # ones. Each one outranks the per-directory metadata, so a stale one left in
    # the driver's environment would stamp every treatment of a sweep with a
    # single identity -- the bug this scoping exists to prevent. Every envar the
    # converters consult has to be scoped, not just the identity pair.
    scoped_env = {
        "LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR": str(results_dir),
        "LLMDBENCH_RUN_EXPERIMENT_ID": _recorded_for(results_dir, "experiment_id"),
        "LLMDBENCH_DESCRIPTION_TEXT": _recorded_for(results_dir, "description_text"),
        "LLMDBENCH_DESCRIPTION_KEYWORDS": _recorded_for(
            results_dir, "description_keywords"
        ),
    }
    previous_env = {name: os.environ.get(name) for name in scoped_env}
    os.environ.update(scoped_env)
    _reset_harness_meta_cache()

    errors: list[str] = []
    try:
        for result_file in result_files:
            result_path = Path(result_file)
            fname = result_path.name

            for br_version in ("0.1", "0.2"):
                prefix = (
                    "benchmark_report"
                    if br_version == "0.1"
                    else "benchmark_report_v0.2"
                )
                output_name = f"{prefix},_{fname}.yaml"
                output_path = results_dir / output_name

                err = _convert_to_benchmark_report(
                    result_path,
                    output_path,
                    writer_name,
                    br_version,
                    context,
                )
                if err:
                    errors.append(err)
    finally:
        for name, previous in previous_env.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        _reset_harness_meta_cache()

    # --- 2. Extract summary from stdout.log ---
    marker = _SUMMARY_MARKERS.get(harness_name)
    if marker:
        _extract_summary(results_dir, marker, context)

    # --- 3. Harness-specific post-processing ---
    if harness_name == "inference-perf":
        _run_inference_perf_analyze(results_dir, context)

    # --- 4. Embed metrics + generate plots (if metrics were collected) ---
    metrics_dir = results_dir / "metrics"
    if metrics_dir.exists():
        _embed_metrics_in_reports(metrics_dir, results_dir, context)
        _run_metric_visualizations(metrics_dir, results_dir, context)

    # --- 5. Generate per-request distribution plots ---
    _run_per_request_plots(results_dir, context)

    # --- 6. Generate session lifecycle plots (inference-perf only) ---
    if harness_name == "inference-perf":
        _run_session_plots(results_dir, context)

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
    _log(context, f"Converting {result_file.name} to Benchmark Report v{br_version}")

    # Try the Python API first (faster, no subprocess)
    err = _convert_via_api(result_file, output_file, writer_name, br_version)
    if err is None:
        return None  # Success

    # Fallback to CLI
    _log(context, f"API conversion failed ({err}), trying CLI fallback...")
    return _convert_via_cli(result_file, output_file, writer_name, br_version)


def _is_session_lifecycle_file(result_file: Path) -> bool:
    return result_file.name.endswith("_session_lifecycle_metrics.json")


def _convert_via_api(
    result_file: Path,
    output_file: Path,
    writer_name: str,
    br_version: str,
) -> str | None:
    """Attempt conversion using the benchmark_report Python API."""
    try:
        if writer_name == "eval-containers":
            # Agentic harness: request/session perf from OTel + reward in
            # results.observability. 0.2-only; skip other versions quietly.
            if br_version != "0.2":
                return None
            from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
                import_eval_containers,
            )

            import_eval_containers(str(result_file)).export_yaml(str(output_file))
            return None

        if br_version == "0.1":
            from llmdbenchmark.analysis.benchmark_report.native_to_br0_1 import (
                import_inference_perf,
                import_inference_perf_session,
                import_guidellm,
                import_vllm_benchmark,
                import_inference_max,
            )
        elif br_version == "0.2":
            from llmdbenchmark.analysis.benchmark_report.native_to_br0_2 import (
                import_inference_perf,
                import_inference_perf_session,
                import_guidellm,
                import_vllm_benchmark,
                import_inference_max,
            )
        else:
            return f"Unsupported BR version: {br_version}"

        if writer_name == "inference-perf" and _is_session_lifecycle_file(result_file):
            convert_fn = import_inference_perf_session
        else:
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

    # SystemExit too: the converters share a CLI entry point whose input check calls
    # sys.exit, and an input that only exists inside the archive trips it. Escaping
    # here would kill the analysis phase instead of degrading to a warning.
    except (Exception, SystemExit) as exc:
        # str(SystemExit(2)) is just "2", which says nothing in a log.
        if isinstance(exc, SystemExit):
            return f"converter exited {exc.code}"
        return str(exc)


def _convert_via_cli(
    result_file: Path,
    output_file: Path,
    writer_name: str,
    br_version: str,
) -> str | None:
    """Fallback: call the ``benchmark-report`` CLI."""
    try:
        cmd = [
            "benchmark-report",
            str(result_file),
            "-b",
            br_version,
            "-w",
            writer_name,
        ]
        if writer_name == "inference-perf" and _is_session_lifecycle_file(result_file):
            cmd.append("-s")
        cmd.append(str(output_file))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return f"benchmark-report exited {result.returncode}: {result.stderr[:200]}"
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
    marker: str | None,
    context: ExecutionContext | None,
) -> None:
    """Extract the tail of stdout.log from *marker* into analysis/summary.txt."""
    try:
        summary_path = extract_summary(results_dir, marker)
    except Exception as exc:
        _log(context, f"Could not extract summary: {exc}", warning=True)
        return

    if summary_path is not None:
        _log(context, f"Summary extracted to {summary_path.name}")


# ---------------------------------------------------------------------------
# inference-perf specific post-processing
# ---------------------------------------------------------------------------


def _run_inference_perf_analyze(
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Run ``inference-perf --analyze`` if available (matches bash script)."""
    if not shutil.which("inference-perf"):
        _log(context, "inference-perf CLI not on PATH -- skipping --analyze")
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
# Metric visualization (Prometheus time series to PNG plots)
# ---------------------------------------------------------------------------


def _embed_metrics_in_reports(
    metrics_dir: Path,
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Merge scraped metrics into the v0.2 reports, clipped per stage.

    The in-pod analyzers run the same pass, via the same module, before the results
    are collected -- so on a normal run this is a no-op re-do. It stays for the
    result sets the pod did not analyse: an older image, or a harness with no in-pod
    analyzer.
    """
    from llmdbenchmark.analysis.metrics_embed import embed_metrics

    embed_metrics(
        metrics_dir,
        results_dir,
        log=lambda message, warning=False: _log(context, message, warning=warning),
    )


def _run_metric_visualizations(
    metrics_dir: Path,
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Generate PNG plots for collected Prometheus metrics.

    Reads ``metrics/raw/*.log`` files and writes PNG graphs to
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
    # Plain files only, and the in-pod pass runs before compression: on the driver
    # this is a fallback that no-ops on an already-compressed result set.
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
# Session lifecycle plot generation
# ---------------------------------------------------------------------------


def _run_session_plots(
    results_dir: Path,
    context: ExecutionContext | None,
) -> None:
    """Generate bar charts for session lifecycle metrics from benchmark report v0.2 files."""
    from llmdbenchmark.analysis.session_plots import generate_session_plots

    try:
        out_dir = results_dir / "analysis" / "session"
        count = generate_session_plots(results_dir, output_dir=out_dir)
        if count:
            _log(context, f"Generated {count} session plot(s) in {out_dir}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _log(context, f"Session plot generation failed: {exc}", warning=True)


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
