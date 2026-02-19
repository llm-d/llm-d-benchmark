#!/usr/bin/env python3
"""
Integrate collected metrics into an existing benchmark report.

This script reads a benchmark report, adds metrics from the collected data,
and saves the updated report.
"""

import argparse
import os
import sys

from .core import import_benchmark_report
from .metrics_processor import add_metrics_to_benchmark_report


def main():
    """Main entry point for metrics integration."""
    parser = argparse.ArgumentParser(
        description="Integrate collected metrics into benchmark report"
    )
    parser.add_argument(
        "benchmark_report",
        help="Path to existing benchmark report (YAML or JSON)",
    )
    parser.add_argument(
        "metrics_dir",
        help="Directory containing collected metrics",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file path (default: overwrite input file)",
    )
    parser.add_argument(
        "-c",
        "--component-label",
        default="vllm-service",
        help="Component label for metrics (default: vllm-service)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="Output format (default: yaml)",
    )

    args = parser.parse_args()

    # Check if files exist
    if not os.path.exists(args.benchmark_report):
        sys.stderr.write(
            f"Error: Benchmark report not found: {args.benchmark_report}\n")
        sys.exit(1)

    if not os.path.exists(args.metrics_dir):
        sys.stderr.write(
            f"Error: Metrics directory not found: {args.metrics_dir}\n")
        sys.exit(1)

    # Import benchmark report
    try:
        br = import_benchmark_report(args.benchmark_report)
        print(f"Loaded benchmark report from: {args.benchmark_report}")
    except Exception as e:
        sys.stderr.write(f"Error loading benchmark report: {e}\n")
        sys.exit(1)

    # Convert to dict and add metrics
    try:
        br_dict = br.dump()
        br_dict = add_metrics_to_benchmark_report(
            br_dict,
            args.metrics_dir,
            args.component_label
        )
        print(f"Added metrics from: {args.metrics_dir}")
    except Exception as e:
        sys.stderr.write(f"Error adding metrics: {e}\n")
        sys.exit(1)

    # Reload as BenchmarkReport to validate
    try:
        from .core import load_benchmark_report
        br_updated = load_benchmark_report(br_dict)
    except Exception as e:
        sys.stderr.write(f"Error validating updated report: {e}\n")
        sys.exit(1)

    # Determine output path
    output_path = args.output if args.output else args.benchmark_report

    # Save updated report
    try:
        if args.format == "json":
            br_updated.export_json(output_path)
        else:
            br_updated.export_yaml(output_path)
        print(f"Saved updated benchmark report to: {output_path}")
    except Exception as e:
        sys.stderr.write(f"Error saving report: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

# Made with Bob
