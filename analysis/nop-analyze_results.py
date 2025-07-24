#!/usr/bin/env python3

"""
Startup logs benchmark
"""

from dataclasses import dataclass, fields
from enum import StrEnum
import io
import os
import logging
from typing import Any
import pandas

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60.0  # time (seconds) to wait for request
MAX_VLLM_WAIT = 15.0 * 60.0  # time (seconds) to wait for vllm to respond


class LoadFormat(StrEnum):
    """Type of model formats"""

    UNKNOWN = "unknown"
    AUTO = "auto"
    PT = "pt"
    SAFETENSORS = "safetensors"
    NPCACHE = "npcache"
    DUMMY = "dummy"
    TENSORIZER = "tensorizer"
    SHARDED_STATE = "sharded_state"
    GGUF = "gguf"
    BITSANDBYTES = "bitsandbytes"
    MISTRAL = "mistral"
    RUNAI_STREAMER = "runai_streamer"
    RUNAI_STREAMER_SHARDED = "runai_streamer_sharded"
    FASTSAFETENSORS = "fastsafetensors"


@dataclass
class LogResult:
    """Results of one benchmark run"""

    time: str = ""
    vllm_version: str = ""
    sleep_mode: bool = False
    model: str = ""
    load_format: LoadFormat = LoadFormat.UNKNOWN
    load_time: float = 0.0
    size: float = 0.0
    sleep: float = 0.0
    gpu_freed: float = 0.0
    gpu_in_use: float = 0.0
    wake: float = 0.0

    @staticmethod
    def header() -> list[str]:
        """header for table print"""
        return [
            "Time",
            "vLLM Version",
            "Sleep/Wake",
            "Model",
            "Load Format",
            "Elapsed(secs)",
            "Rate(GB/s)",
            "Sleep(secs)",
            "Freed GPU(GiB)",
            "In Use GPU(GiB)",
            "Wake(secs)",
        ]

    def row(self) -> list[str]:
        """row for table print"""
        return [
            self.time,
            self.vllm_version,
            str(self.sleep_mode),
            self.model,
            str(self.load_format),
            f"{self.load_time:.2f}",
            f"{self.transfer_rate():.2f}",
            f"{self.sleep:.2f}",
            f"{self.gpu_freed:.2f}",
            f"{self.gpu_in_use:.2f}",
            f"{self.wake:.2f}",
        ]

    @staticmethod
    def header_csv() -> list[str]:
        """csv header"""
        header = []
        for field in fields(LogResult):
            header.append(field.name)

        return header

    def row_csv(self) -> list[Any]:
        """csv row"""
        row = []
        for name in LogResult.header_csv():
            row.append(getattr(self, name))

        return row

    def transfer_rate(self) -> float:
        """calculate GB/s"""
        if self.load_time > 0:
            return self.size / self.load_time
        return 0.0


def get_env_variables(keys: list[str]) -> list[str]:
    """get environment variables"""

    logger.info("Environment variables:")

    env_vars = os.environ

    envs = []
    missing_envs = []
    for key in keys:
        value = env_vars.get(key)
        if value is None:
            missing_envs.append(key)
        else:
            envs.append(value)
            logger.info("  '%s': '%s'", key, value)

    if len(missing_envs) > 0:
        raise RuntimeError(f"Env. variables not found: {','.join(missing_envs)}.")
    return envs


def write_log_results(file: io.TextIOWrapper, log_results: list[LogResult]):
    """writes benchmark results"""

    model_results_table = [LogResult.header()]
    for log_result in log_results:
        model_results_table.append(log_result.row())

    write_table(file, model_results_table)


def write_table(file: io.TextIOWrapper, table: list[list[str]]) -> None:
    """write a matrix with header and rows in table format"""
    if len(table) == 0:
        return

    longest_cols = [len(max(col, key=len)) + 3 for col in zip(*table)]
    row_format = "".join(
        ["{:>" + str(longest_col) + "}" for longest_col in longest_cols]
    )
    # write header
    header = table[0]
    file.write(f"{row_format.format(*header)}\n")
    row_underline = ["-" * longest_col for longest_col in longest_cols]
    # write underline
    file.write(f"{row_format.format(*row_underline)}\n")
    # write rows
    for row in table[1:]:
        file.write(f"{row_format.format(*row)}\n")


def read_log_results_from_csv(file_path: str) -> list[LogResult]:
    """read csv log results"""

    log_results = []
    if not os.path.isfile(file_path):
        logger.info("no csv file found on path: %s", file_path)
        return log_results

    df = pandas.read_csv(file_path, encoding="utf-8")

    log_result_header = LogResult.header_csv()

    for _, row in df.iterrows():
        log_result = LogResult()
        for name in log_result_header:
            value = row.get(name)
            if value is not None:
                setattr(log_result, name, value)
        log_results.append(log_result)

    logger.info("csv file found on path: %s", file_path)
    return log_results


def main():
    """main entry point"""

    envs = get_env_variables(
        [
            "LLMDBENCH_CONTROL_WORK_DIR",
        ]
    )

    control_work_dir = envs[0]
    requests_dir = control_work_dir

    cvs_filepath = os.path.join(requests_dir, "nop.csv")

    # read possible existent csv file
    log_results = read_log_results_from_csv(cvs_filepath)
    if len(log_results) == 0:
        logger.info("no csv file available for analysis")
        return

    # write analysis file
    analysis_dir = os.path.join(requests_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    analysis_filepath = os.path.join(analysis_dir, "nop.txt")
    with open(analysis_filepath, "w", encoding="utf-8") as file:
        write_log_results(file, log_results)
        logger.info("analysis file saved to path: %s", analysis_filepath)


if __name__ == "__main__":
    try:
        logger.info("Starting analysis run")
        main()
    except Exception as e:
        logger.error("Error running analysis: %s", str(e))
    finally:
        logger.info("End analysis run")
