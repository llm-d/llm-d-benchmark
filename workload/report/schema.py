#!/usr/bin/env python3

import json
from typing import Any

import yaml

try:
    from benchmark_report import BenchmarkReport
    from benchmark_report_v0_1 import BenchmarkReportV01
    from benchmark_report_v0_2 import BenchmarkReportV02
except ImportError:
    from config_explorer.benchmark_report import BenchmarkReport
    from config_explorer.benchmark_report_v0_1 import BenchmarkReportV01
    from config_explorer.benchmark_report_v0_2 import BenchmarkReportV02


def load_benchmark_report(data: dict[str, Any]) -> BenchmarkReport:
    """
    Auto-detect schema version and load the appropriate benchmark report model.

    Args:
        data (dict[str, Any]): Benchmark report data as a dict.

    Returns:
        BenchmarkReport: Populated instance of benchmark report of appropriate
            version.
    """
    version = data.get("version")

    if version == "0.1":
        return BenchmarkReportV01(**data)
    elif version == "0.2":
        return BenchmarkReportV02(**data)
    else:
        raise ValueError(f"Unsupported schema version: {version}")


def make_json_schema(version: str = "0.2") -> str:
    """
    Create a JSON schema for the benchmark report.

    Returns:
        str: JSON schema of benchmark report.
    """
    if version == "0.1":
        return json.dumps(BenchmarkReportV01.model_json_schema(), indent=2)
    elif version == "0.2":
        return json.dumps(BenchmarkReportV02.model_json_schema(), indent=2)
    else:
        raise ValueError(f"Unsupported schema version: {version}")


def create_from_str(yaml_str: str) -> BenchmarkReport:
    """
    Create a BenchmarkReport instance from a JSON/YAML string.

    Args:
        yaml_str (str): JSON/YAML string to import.

    Returns:
        BenchmarkReport: Instance with values from string.
    """
    return load_benchmark_report(yaml.safe_load(yaml_str))

# If this is executed directly, print JSON schema.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Print JSON Schema for Benchmark Report."
    )
    parser.add_argument(
        "version",
        nargs="?",
        default="0.2",
        type=str,
        help="Benchmark report version"
    )
    
    args = parser.parse_args()
    print(make_json_schema(args.version))
