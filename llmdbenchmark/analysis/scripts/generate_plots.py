#!/usr/bin/env python3
"""In-pod entry point for the per-request and session-lifecycle plots.

These two plot sets were the only analysis stages left running solely on the
driver, which forced the collected results to be expanded locally just to be
plotted. Producing them here keeps the pod the single producer of every
artifact, so collection stays a pure transfer.

Usage: generate_plots.py <results_dir>
"""

import sys
from pathlib import Path

from per_request_plots import generate_per_request_plots
from session_plots import generate_session_plots


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <results_dir>", file=sys.stderr)
        return 2

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"not a directory: {results_dir}", file=sys.stderr)
        return 1

    for label, generate in (
        ("per-request distribution", generate_per_request_plots),
        ("session lifecycle", generate_session_plots),
    ):
        # One failing set must not cost the other: the harness has already run,
        # and a missing plot is not worth failing a completed benchmark over.
        try:
            count = generate(results_dir)
            print(f"Generated {count} {label} plot(s)")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"WARNING: {label} plots failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
