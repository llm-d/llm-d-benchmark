"""
Core functions for benchmark reports.
"""

import json
import os
import sys
from typing import Any

import yaml
import numpy as np

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
        sys.exit(2)
    if not os.path.isfile(file_path):
        sys.stderr.write(f"Not a regular file: {file_path}\n")
        sys.exit(2)


def import_csv_with_header(file_path: str) -> dict[str, list[Any]]:
    """Import a CSV file where the first line is a header.

    Args:
        file_path (str): Path to CSV file.

    Returns:
        dict: Imported data where the header provides key names.
    """
    with open(file_path, "r", encoding="UTF-8") as file:
        for ii, line in enumerate(file):
            if ii == 0:
                headers: list[str] = list(map(str.strip, line.split(",")))
                data: dict[str, list[Any]] = {}
                for hdr in headers:
                    data[hdr] = []
                continue
            row_vals = list(map(str.strip, line.split(",")))
            if len(row_vals) != len(headers):
                sys.stderr.write(
                    f'Warning: line {ii + 1} of "{file_path}" does not match '
                    f"header length, skipping: {len(row_vals)} != {len(headers)}\n"
                )
                continue
            for jj, val in enumerate(row_vals):
                # Try converting the value to an int or float
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
                data[headers[jj]].append(val)
    # Convert lists of ints or floats to numpy arrays
    for hdr in headers:
        if isinstance(data[hdr][0], int, float):
            data[hdr] = np.array(data[hdr])
    return data


def get_nested(ndict: dict[Any, Any], path: list[Any], default: Any = None) -> Any:
    """Get value from path through nested dicts.

    Args:
        ndict (dict): Nested dict to get value from.
        path (list): Path through nested dict, as a list of keys.
        default (Any): Value to return if path does not exist.

    Returns:
        Any: Value at path location, or default value if path does not exist.
    """

    d_cur = ndict
    for key in path:
        if not isinstance(d_cur, dict):
            # Path hit a non-dict
            return default
        if key not in d_cur:
            # Key is not in dict
            return default
        d_cur = d_cur[key]
    return d_cur


def update_dict(dest: dict[Any, Any], source: dict[Any, Any]) -> None:
    """Deep update a dict using values from another dict. If a value is a dict,
    then update that dict, otherwise overwrite with the new value.

    Args:
        dest (dict): dict to update.
        source (dict): dict with new values to add to dest.
    """
    for key, val in source.items():
        if key in dest and isinstance(dest[key], dict):
            if not val:
                # Do not "update" with null values
                continue
            if not isinstance(val, dict):
                raise TypeError(f"Cannot update dict type with non-dict: {val}")
            update_dict(dest[key], val)
        else:
            dest[key] = val


def import_yaml(file_path: str) -> dict[Any, Any]:
    """Import a JSON/YAML file as a dict.

    Args:
        file_path (str): Path to JSON/YAML file.

    Returns:
        dict: Imported data.
    """
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
    if version == "0.2":
        return BenchmarkReportV02(**data)
    raise ValueError(f"Unsupported schema version: {version}")


def import_benchmark_report(br_file: str) -> BenchmarkReport:
    """Import benchmark report from a JSON or YAML file.

    Args:
        br_file (str): Benchmark report file to import.

    Returns:
        BenchmarkReport: Imported benchmark report supplemented with run data.
    """
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
    if version == "0.2":
        return json.dumps(BenchmarkReportV02.model_json_schema(), indent=2)
    raise ValueError(f"Unsupported schema version: {version}")
