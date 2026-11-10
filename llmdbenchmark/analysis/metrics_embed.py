"""Embed scraped metrics into the v0.2 reports, clipped to each stage's window.

Shared by the driver and the in-pod analyzers. One scrape covers a whole run, so
without a per-stage window every stage report carries the whole-run series -- which
is what the in-pod scripts used to produce, since they called
``add_metrics_to_benchmark_report`` with no window at all.

Runs in-pod, before the results are compressed, so every input here is a plain
file: ``metrics/raw/`` and ``stdout.log`` for the stage bounds,
``metrics/processed/`` for the scrape, and the reports themselves.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

STAGE_MARKER_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ "
    r".*Stage (\d+) - (?:session-based )?run (started|completed|failed)",
    re.MULTILINE,
)
# inference-perf's own stamps, served on its /metrics and scraped by collect_metrics.sh.
SCRAPED_STAGE_RE = re.compile(
    r'^inference_perf_stage_(start|end)_timestamp_seconds\{[^}]*\bstage="(\d+)"[^}]*\} (\S+)',
    re.MULTILINE,
)
HARNESS_SOURCE = "# Source: harness_prometheus_metrics"
# Greedy, to take the last occurrence like native_to_br0_2 does on the same filename.
REPORT_STAGE_RE = re.compile(r".*stage_(\d+)", re.DOTALL)


def stage_windows(
    results_dir: Path, metrics_dir: Path | None = None
) -> dict[int, tuple[datetime, datetime]]:
    """Map stage index to its (start, end) as reported by the load generator.

    A stage the harness scrape stamped (see :func:`scraped_stage_windows`) takes
    those bounds. The others fall back to the log markers, which are
    ``%(asctime)s`` local time cut to the second, stamped UTC here because the pod
    pins ``TZ=UTC`` (20_harness_pod.yaml.j2). Returns {} when neither source holds
    a complete pair, leaving the caller on the whole-run series.
    """
    windows = _logged_stage_windows(results_dir)
    if metrics_dir is not None:
        windows.update(scraped_stage_windows(metrics_dir))
    return windows


def scraped_stage_windows(metrics_dir: Path) -> dict[int, tuple[datetime, datetime]]:
    """Map stage index to its (start, end) from inference-perf's scraped stamps.

    Only scrapes of the harness's own endpoint are read. The stamps are Unix times
    with sub-second precision. A restarted stage is stamped again, so the latest
    stamp wins, as the last log marker does.
    """
    stamps: dict[int, dict[str, float]] = {}
    for path in (metrics_dir / "raw").glob("*.log"):
        try:
            with open(path, errors="replace") as fh:
                header = [fh.readline() for _ in range(5)]
                if not any(line.strip() == HARNESS_SOURCE for line in header):
                    continue
                text = fh.read()
        except OSError:
            continue
        for kind, stage, value in SCRAPED_STAGE_RE.findall(text):
            try:
                when = float(value)
            except ValueError:
                continue
            if not math.isfinite(when):
                continue
            pair = stamps.setdefault(int(stage), {})
            pair[kind] = max(pair.get(kind, when), when)

    return {
        stage: (
            datetime.fromtimestamp(pair["start"], timezone.utc),
            datetime.fromtimestamp(pair["end"], timezone.utc),
        )
        for stage, pair in stamps.items()
        if "start" in pair and "end" in pair and pair["start"] < pair["end"]
    }


def _logged_stage_windows(results_dir: Path) -> dict[int, tuple[datetime, datetime]]:
    try:
        text = (results_dir / "stdout.log").read_text(errors="replace")
    except OSError:
        return {}

    bounds: dict[int, dict[str, datetime]] = {}
    for stamp, stage, event in STAGE_MARKER_RE.findall(text):
        try:
            when = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        key = "started" if event == "started" else "ended"
        bounds.setdefault(int(stage), {})[key] = when

    # A retried or restarted stage can log its markers out of order, which would
    # clip every sample away; fall back to the whole run instead.
    return {
        stage: (pair["started"], pair["ended"])
        for stage, pair in bounds.items()
        if "started" in pair and "ended" in pair and pair["started"] < pair["ended"]
    }


def embed_metrics(
    metrics_dir: Path,
    results_dir: Path,
    log=None,
) -> int:
    """Merge metrics into every v0.2 report under *results_dir*, stage-clipped.

    Returns the number of reports written. *log* takes ``(message, warning)``.
    """

    def _say(message: str, warning: bool = False) -> None:
        if log is not None:
            log(message, warning)

    import yaml

    try:
        from llmdbenchmark.analysis.benchmark_report.metrics_processor import (
            add_metrics_to_benchmark_report,
            load_scraped_time_series,
        )
    except ImportError:  # in-pod: the library is installed, the package is not
        from benchmark_report.metrics_processor import (  # type: ignore[no-redef]
            add_metrics_to_benchmark_report,
            load_scraped_time_series,
        )

    if not (metrics_dir / "processed" / "metrics_summary.json").is_file():
        _say("No metrics summary -- skipping report metrics embedding")
        return 0

    reports = sorted(results_dir.glob("benchmark_report_v0.2*,_*.yaml"))
    if not reports:
        return 0

    windows = stage_windows(results_dir, metrics_dir)

    staged = [r for r in reports if REPORT_STAGE_RE.search(r.name)]
    # Staged reports with no window at all means the markers stopped parsing,
    # which silently restores the whole-run series this clipping replaced.
    if staged and not windows:
        _say(
            f"No stage windows in the harness scrape or stdout.log for {len(staged)} stage "
            f"report(s) -- embedding the whole run in each",
            True,
        )

    # Parsed once for every report: the scrapes can be large.
    scraped_series = load_scraped_time_series(str(metrics_dir))
    written = 0
    for report in reports:
        try:
            stage = REPORT_STAGE_RE.search(report.name)
            window = windows.get(int(stage.group(1))) if stage else None
            # Warns only for a stage missing from an otherwise-parsed set; the
            # none-parsed case is reported once above.
            if stage and window is None and windows:
                _say(
                    f"No stage window for {report.name} -- embedding the whole run",
                    True,
                )

            br_dict = yaml.safe_load(report.read_text()) or {}
            br_dict = add_metrics_to_benchmark_report(
                br_dict,
                str(metrics_dir),
                time_series_window=window,
                scraped_series=scraped_series,
            )

            interval = br_dict.get("results", {}).get("observability", {})
            interval = interval.get("time_series_interval", {})
            if (
                window
                and not interval.get("datapoints")
                and interval.get("datapoints_available")
            ):
                _say(
                    f"Stage window {interval.get('start')}..{interval.get('end')} "
                    f"kept none of {interval.get('datapoints_available')} datapoints "
                    f"scraped over {interval.get('scraped_from')}.."
                    f"{interval.get('scraped_to')} in {report.name} -- series empty",
                    True,
                )

            with open(report, "w", encoding="utf-8") as fh:
                yaml.dump(br_dict, fh, default_flow_style=False, allow_unicode=True)
            _say(f"Embedded metrics into {report.name}")
            written += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _say(f"Metrics embedding failed for {report.name}: {exc}", True)

    return written
