#!/usr/bin/env python3

"""
Startup logs benchmark
"""

from __future__ import annotations
from dataclasses import dataclass, fields
from enum import StrEnum
import io
import os
import logging
from typing import Any
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60.0  # time (seconds) to wait for request
MAX_VLLM_WAIT = 15.0 * 60.0  # time (seconds) to wait for vllm to respond


@dataclass
class LogCategory:
    """Log category"""

    title: str = ""
    elapsed: float = 0.0
    next: LogCategory | None = None
    parent: LogCategory | None = None
    root_child: LogCategory | None = None

    def to_dataframe(self) -> pd.DataFrame:
        """dataframe for table print"""
        elapsed_str = ""
        if self.elapsed != 0:
            elapsed_str = f"{self.elapsed:.3f}"
        data = {
            "Category": [self.title],
            "Elapsed(secs)": [elapsed_str],
        }
        return pd.DataFrame(data)


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

    def to_dataframe(self) -> pd.DataFrame:
        """dataframe for table print"""
        data = {
            "Time": [self.time],
            "vLLM Version": [self.vllm_version],
            "Sleep/Wake": [str(self.sleep_mode)],
            "Model": [self.model],
            "Load Format": [str(self.load_format)],
            "Elapsed(secs)": [f"{self.load_time:.2f}"],
            "Rate(GB/s)": [f"{self.transfer_rate():.2f}"],
            "Sleep(secs)": [f"{self.sleep:.2f}"],
            "Freed GPU(GiB)": [f"{self.gpu_freed:.2f}"],
            "In Use GPU(GiB)": [f"{self.gpu_in_use:.2f}"],
            "Wake(secs)": [f"{self.wake:.2f}"],
        }
        return pd.DataFrame(data)

    @staticmethod
    def header_csv() -> list[str]:
        """csv header"""
        header = []
        for f in fields(LogResult):
            header.append(f.name)

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


def get_formatted_output(column: str, df: pd.DataFrame) -> str:
    """get formatted output"""
    max_len = df[column].astype(str).str.len().max()
    formatters = {column: lambda x: f"{x:<{max_len}}"}
    df_string = df.to_string(formatters=formatters, index=False)

    lines = df_string.split("\n")
    separator = "-" * len(lines[0])

    # Insert the separator after the header line
    lines.insert(1, separator)

    return f"{'\n'.join(lines)}\n"


def write_log_results(file: io.TextIOWrapper, log_results: list[LogResult]):
    """writes benchmark results"""

    df = pd.DataFrame()
    for log_result in log_results:
        df = pd.concat([df, log_result.to_dataframe()])

    file.write(get_formatted_output("Time", df))


def populate_category_results(
    log_category: LogCategory,
    level: int,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """populates category benchmark results"""

    blank_string = "  " * level if level > 0 else ""
    category = log_category
    total = 0.0
    while category is not None:
        total += category.elapsed
        data = category.to_dataframe()
        data.iloc[0, 0] = blank_string + data.iloc[0, 0]
        df = pd.concat([df, data])

        if category.root_child is not None:
            df = populate_category_results(category.root_child, level + 1, df)
        category = category.next

    df_total = pd.DataFrame(
        {
            "Category": [blank_string + "Total"],
            "Elapsed(secs)": [total],
        }
    )

    # Append the total row to the DataFrame
    return pd.concat([df, df_total])


def write_category_results(file: io.TextIOWrapper, root_log_category: LogCategory):
    """writes category benchmark results"""

    df = populate_category_results(root_log_category, 0, pd.DataFrame())
    file.write(get_formatted_output("Category", df))


def read_log_results_from_csv(file_path: str) -> list[LogResult]:
    """read csv log results"""

    log_results = []
    if not os.path.isfile(file_path):
        logger.info("no csv file found on path: %s", file_path)
        return log_results

    df = pd.read_csv(file_path, encoding="utf-8")

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


def read_log_categories_from_csv(file_path: str) -> LogCategory:
    """read csv log categories"""

    root_log_category = None
    if not os.path.isfile(file_path):
        logger.info("no csv categories file found on path: %s", file_path)
        return root_log_category

    df = pd.read_csv(file_path, dtype=str, keep_default_na=False)

    # Group children by their parent
    tree = df.groupby("parent")["key"].apply(list).to_dict()

    root_log_category = populate_log_categories("", tree, df, None)

    logger.info("csv categories file found on path: %s", file_path)
    return root_log_category


def populate_log_categories(
    key: str, tree: pd.Dataframe, df: pd.DataFrame, parent: LogCategory
) -> LogCategory:
    """populates categories from dataframes"""

    root_log_category = None
    prev_log_category = None
    for child_key in tree.get(key, []):
        category_row = df[df["key"] == child_key].iloc[0]
        log_category = LogCategory()
        if root_log_category is None:
            root_log_category = log_category
        if prev_log_category is not None:
            prev_log_category.next = log_category
        prev_log_category = log_category
        log_category.title = category_row.get("title")
        elapsed_str = category_row.get("elapsed")
        if elapsed_str != "":
            try:
                log_category.elapsed = float(elapsed_str)
            except ValueError:
                logger.exception(
                    "Error converting elapsed value %s to float", elapsed_str
                )
        log_category.parent = parent
        if log_category.parent is not None and log_category.parent.root_child is None:
            log_category.parent.root_child = log_category

        _ = populate_log_categories(child_key, tree, df, log_category)

    return root_log_category


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

    analysis_dir = os.path.join(requests_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    # write results analysis file
    analysis_filepath = os.path.join(analysis_dir, "nop.txt")
    with open(analysis_filepath, "w", encoding="utf-8") as file:
        write_log_results(file, log_results)
        logger.info("analysis file saved to path: %s", analysis_filepath)

    # read possible existent csv file
    cvs_filepath = os.path.join(requests_dir, "nop_categories.csv")
    root_log_category = read_log_categories_from_csv(cvs_filepath)
    if root_log_category is None:
        logger.info("no csv category file available for analysis")
        return

    # write categgory analysis file
    category_analysis_filepath = os.path.join(analysis_dir, "nop_categories.txt")
    with open(category_analysis_filepath, "w", encoding="utf-8") as file:
        write_category_results(file, root_log_category)
        logger.info(
            "category analysis file saved to path: %s", category_analysis_filepath
        )


if __name__ == "__main__":
    try:
        logger.info("Starting analysis run")
        main()
    except Exception:
        logger.exception("Error running analysis")
    finally:
        logger.info("End analysis run")
