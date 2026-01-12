"""
Convert application native output formats into a Benchmark Report.
"""

import base64
import datetime
import os
import re
import sys
from typing import Any
import yaml

import numpy as np

from .base import BenchmarkReport, Units
from .core import check_file, import_yaml, load_benchmark_report
from .schema_v0_1 import WorkloadGenerator, HostType


def import_csv_with_header(file_path: str) -> dict[str, list[Any]]:
    """Import a CSV file where the first line is a header.

    Args:
        file_path (str): Path to CSV file.

    Returns:
        dict: Imported data where the header provides key names.
    """
    check_file(file_path)
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
                    'Warning: line %d of "%s" does not match header length, skipping: %d != %d\n'
                    % (ii + 1, file_path, len(row_vals), len(headers))
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
        if isinstance(data[hdr][0], int) or isinstance(data[hdr][0], float):
            data[hdr] = np.array(data[hdr])
    return data


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
                raise Exception("Cannot update dict type with non-dict: %s" % val)
            update_dict(dest[key], val)
        else:
            dest[key] = val


def _get_llmd_benchmark_envars() -> dict:
    """Get information from environment variables for the benchmark report.

    Returns:
        dict: Imported data about scenario following schema of BenchmarkReport.
    """
    br_dict = {
        "version": "0.2",
        "run":
LLMDBENCH_HARNESS_START
    }

    # We make the assumption that if the environment variable
    # LLMDBENCH_MAGIC_ENVAR is defined, then we are inside a harness pod.
    if "LLMDBENCH_MAGIC_ENVAR" not in os.environ:
        # We are not in a harness pod
        return {}

    if "LLMDBENCH_DEPLOY_METHODS" not in os.environ:
        sys.stderr.write(
            "Warning: LLMDBENCH_DEPLOY_METHODS undefined, cannot determine deployment method."
        )
        return {}

    if os.environ["LLMDBENCH_DEPLOY_METHODS"] == "standalone":
        # Given a 'standalone' deployment, we expect the following environment
        # variables to be available
        return {
            "scenario": {}
        }