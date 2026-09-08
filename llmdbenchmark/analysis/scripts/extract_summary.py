#!/usr/bin/env python3
"""In-pod entry point for analysis/summary.txt.

Usage: extract_summary.py <results_dir> [marker]
"""

import sys
from pathlib import Path

try:
    from llmdbenchmark.analysis.summary import extract_summary
except ImportError:  # in-pod: shipped flat, no llmdbenchmark package
    from summary import extract_summary


def main() -> int:
    if not 2 <= len(sys.argv) <= 3:
        print(f"usage: {sys.argv[0]} <results_dir> [marker]", file=sys.stderr)
        return 2

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"not a directory: {results_dir}", file=sys.stderr)
        return 1

    marker = sys.argv[2] if len(sys.argv) == 3 else None
    written = extract_summary(results_dir, marker)
    if written is None:
        print("No summary extracted (no stdout.log, or marker not found)")
    else:
        print(f"Summary extracted to {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
