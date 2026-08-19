#!/usr/bin/env python3
"""In-pod entry point for stage-clipped metrics embedding.

The analyzers used to inline this as a python -c one-liner that called
``add_metrics_to_benchmark_report`` with no ``time_series_window``, so every stage
report carried the whole run's series. Clipping lived only on the driver, which no
longer re-analyses a result set the pod already handled.

Usage: embed_metrics.py <results_dir>
"""

import sys
from pathlib import Path

try:
    from llmdbenchmark.analysis.metrics_embed import embed_metrics
except ImportError:  # in-pod: shipped flat, no llmdbenchmark package
    from metrics_embed import embed_metrics


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <results_dir>", file=sys.stderr)
        return 2

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"not a directory: {results_dir}", file=sys.stderr)
        return 1

    def log(message: str, warning: bool = False) -> None:
        print(
            f"{'WARNING: ' if warning else ''}{message}",
            file=sys.stderr if warning else sys.stdout,
        )

    count = embed_metrics(results_dir / "metrics", results_dir, log=log)
    print(f"Metrics embedded into {count} report(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
