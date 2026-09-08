"""Embed scraped metrics into the v0.2 reports, clipped to each stage's window.

Shared by the driver and the in-pod analyzers. One scrape covers a whole run, so
without a per-stage window every stage report carries the whole-run series -- which
is what the in-pod scripts used to produce, since they called
``add_metrics_to_benchmark_report`` with no window at all.

Runs in-pod, before the results are compressed, so every input here is a plain
file: ``stdout.log`` for the markers, ``metrics/processed/`` for the scrape, and
the reports themselves.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

STAGE_MARKER_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ "
    r".*Stage (\d+) - (?:session-based )?run (started|completed|failed)",
    re.MULTILINE,
)
# Greedy, to take the last occurrence like native_to_br0_2 does on the same filename.
REPORT_STAGE_RE = re.compile(r".*stage_(\d+)", re.DOTALL)


def stage_windows(results_dir: Path) -> dict[int, tuple[datetime, datetime]]:
    """Map stage index to its (start, end) as logged by the load generator.

    The markers are ``%(asctime)s`` local time, stamped UTC here because the pod
    pins ``TZ=UTC`` (20_harness_pod.yaml.j2). Returns {} when the log is missing
    or holds no complete marker pair, leaving the caller on the whole-run series.
    """
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
        )
    except ImportError:  # in-pod: the library is installed, the package is not
        from benchmark_report.metrics_processor import (  # type: ignore[no-redef]
            add_metrics_to_benchmark_report,
        )

    if not (metrics_dir / "processed" / "metrics_summary.json").is_file():
        _say("No metrics summary -- skipping report metrics embedding")
        return 0

    reports = sorted(results_dir.glob("benchmark_report_v0.2,_*.yaml"))
    if not reports:
        return 0

    windows = stage_windows(results_dir)

    staged = [r for r in reports if REPORT_STAGE_RE.search(r.name)]
    # Staged reports with no window at all means the markers stopped parsing,
    # which silently restores the whole-run series this clipping replaced.
    if staged and not windows:
        _say(
            f"No stage windows parsed from stdout.log for {len(staged)} stage "
            f"report(s) -- embedding the whole run in each",
            True,
        )

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
                br_dict, str(metrics_dir), time_series_window=window
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
