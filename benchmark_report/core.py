#!/usr/bin/env python3

import json
import os
import sys
from typing import Any

import yaml

from .base import BenchmarkReport
from .schema_v0_1 import BenchmarkReportV01
from .schema_v0_2 import BenchmarkReportV02


def check_file(file_path: str) -> None:
    """Make sure regular file exists.

    Args:
        file_path (str): File to check.
    """
    if not os.path.exists(file_path):
        sys.stderr.write(f"File does not exist: {file_path}\n")
        exit(2)
    if not os.path.isfile(file_path):
        sys.stderr.write(f"Not a regular file: {file_path}\n")
        exit(2)


def import_yaml(file_path: str) -> dict[Any, Any]:
    """Import a JSON/YAML file as a dict.

    Args:
        file_path (str): Path to JSON/YAML file.

    Returns:
        dict: Imported data.
    """
    check_file(file_path)
    with open(file_path, "r", encoding="UTF-8") as file:
        data = yaml.safe_load(file)
    return data


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


def import_benchmark_report(br_file: str) -> BenchmarkReport:
    """Import benchmark report from a JSON or YAML file.

    Args:
        br_file (str): Benchmark report file to import.

    Returns:
        BenchmarkReport: Imported benchmark report supplemented with run data.
    """
    check_file(br_file)

    # Import benchmark report as a dict following the schema of BenchmarkReport
    br_dict = import_yaml(br_file)

    return load_benchmark_report(br_dict)


def yaml_str_to_benchmark_report(yaml_str: str) -> BenchmarkReport:
    """
    Create a BenchmarkReport instance from a JSON/YAML string.

    Args:
        yaml_str (str): JSON/YAML string to import.

    Returns:
        BenchmarkReport: Instance with values from string.
    """
    return load_benchmark_report(yaml.safe_load(yaml_str))


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


# If this is executed directly, print JSON schema.
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Print JSON Schema for Benchmark Report."
    )
    parser.add_argument(
        "version", nargs="?", default="0.2", type=str, help="Benchmark report version"
    )

    args = parser.parse_args()
    print(make_json_schema(args.version))
