#!/usr/bin/env python3

"""
Benchmark 'nop' analysis
"""

from datetime import datetime
import io
import os
import logging
from typing import Any
import pandas as pd
import yaml

from schema import BenchmarkReport

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")


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


def get_formatted_output(columns: list[str], df: pd.DataFrame) -> str:
    """get formatted output"""
    formatters = {}
    for column in columns:
        max_len = df[column].astype(str).str.len().max()
        formatters[column] = lambda x, max=max_len: f"{x:<{max}}"

    df_string = df.to_string(formatters=formatters, index=False)

    lines = df_string.split("\n")
    separator = "-" * len(lines[0])

    # Insert the separator after the header line
    lines.insert(1, separator)
    line = "\n".join(lines)
    return f"{line}\n"


def create_categories_dataframe(
    categories: list[dict[str, Any]],
    level: int,
    df: pd.DataFrame,
) -> pd.DataFrame:
    """create categories dataframe"""

    blank_string = "  " * level if level > 0 else ""
    total = 0.0
    for category in categories:
        process = category.get("process", "")
        elapsed = category["elapsed"]["value"]
        total += elapsed
        elapsed_str = f"{elapsed:.3f}" if elapsed != 0 else ""
        data = {
            "Category": [category["title"]],
            "Process": [process],
            "Elapsed(secs)": [elapsed_str],
        }
        data = pd.DataFrame(data)
        data.iloc[0, 0] = blank_string + data.iloc[0, 0]
        df = pd.concat([df, data])

        children = category.get("categories")
        if children is not None:
            df = create_categories_dataframe(children, level + 1, df)

    df_total = pd.DataFrame(
        {
            "Category": [blank_string + "Total"],
            "Process": [""],
            "Elapsed(secs)": [f"{total:.3f}"],
        }
    )

    # Append the total row to the DataFrame
    return pd.concat([df, df_total])


def write_benchmark_scenario(file: io.TextIOWrapper, benchmark_report: BenchmarkReport):
    """write benchmark scenario to file"""

    scenario = benchmark_report.scenario
    file.write("Scenario\n")
    file.write(f" Harness       : {scenario.load.name}\n")
    file.write(f" Load Format   : {scenario.metadata['load_format']}\n")
    file.write(f" Sleep Mode On : {scenario.metadata['sleep_mode']}\n")
    file.write(f" Model         : {scenario.model.name}\n")
    for engine in scenario.platform.engine:
        file.write(" Engine\n")
        file.write(f"  Name         : {engine.name}\n")
        file.write(f"  Version      : {engine.version}\n")
        file.write(f"  Args         : {str(engine.args)}\n")


def write_benchmark_reports(file: io.TextIOWrapper, benchmark_report: BenchmarkReport):
    """write benchmark reports to file"""

    write_benchmark_scenario(file, benchmark_report)
    file.write("\n")

    time_iso = (
        datetime.fromtimestamp(benchmark_report.metrics.time.start)
        .astimezone()
        .isoformat()
    )
    duration = benchmark_report.metrics.time.duration

    metrics_metadata = benchmark_report.metrics.metadata
    elapsed = metrics_metadata["load_time"]["value"]
    rate = metrics_metadata["transfer_rate"]["value"]
    sleep = metrics_metadata["sleep"]["value"]
    freed = metrics_metadata["gpu_freed"]["value"]
    use = metrics_metadata["gpu_in_use"]["value"]
    wake = metrics_metadata["wake"]["value"]
    load_cached_compiled_graph = metrics_metadata.get("load_cached_compiled_graph")
    compile_graph = metrics_metadata.get("compile_graph")

    file.write("Benchmark\n")
    file.write(f"  Start                    : {time_iso}\n")
    file.write(f"  Elapsed(secs)            : {duration:7.3f}\n")
    file.write("   Model Load\n")
    file.write(f"     Elapsed(secs)         : {elapsed:7.3f}\n")
    file.write(f"     Rate(GiB/secs)        : {rate:7.3f}\n")
    if load_cached_compiled_graph is not None or compile_graph is not None:
        file.write("   Compiled Graph\n")
        if load_cached_compiled_graph is not None:
            file.write(
                f"     Load from Cache(secs) : {load_cached_compiled_graph['value']:7.3f}\n"
            )
        if compile_graph is not None:
            file.write(f"     Compile(secs)         : {compile_graph['value']:7.3f}\n")
    file.write("   Sleep\n")
    file.write(f"     Elapsed(secs)         : {sleep:7.3f}\n")
    file.write("      Memory GPU(GiB)\n")
    file.write(f"        Freed              : {freed:7.3f}\n")
    file.write(f"        in Use             : {use:7.3f}\n")
    file.write("   Wake\n")
    file.write(f"     Elapsed(secs)         : {wake:7.3f}\n")

    categories = metrics_metadata.get("categories")
    if categories is None:
        return

    file.write("\n")
    data_frame = create_categories_dataframe(categories, 0, pd.DataFrame())
    file.write(get_formatted_output(["Category", "Process"], data_frame))


def main():
    """main entry point"""

    envs = get_env_variables(
        [
            "LLMDBENCH_CONTROL_WORK_DIR",
        ]
    )

    control_work_dir = envs[0]
    requests_dir = control_work_dir

    analysis_dir = os.path.join(requests_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    file_handler = logging.FileHandler(f"{analysis_dir}/stdout.log")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # read possible existent universal yaml file
    benchmark_report_filepath = os.path.join(
        requests_dir, "benchmark_report", "result.yaml"
    )
    if not os.path.isfile(benchmark_report_filepath):
        logger.info(
            "no benchmark reports file found on path: %s", benchmark_report_filepath
        )
        return

    benchmark_report = None
    with open(benchmark_report_filepath, "r", encoding="UTF-8") as file:
        benchmark_dict = yaml.safe_load(file)
        benchmark_report = BenchmarkReport(**benchmark_dict)

    # write reports analysis file
    reports_filepath = os.path.join(analysis_dir, "result.txt")
    with open(reports_filepath, "w", encoding="utf-8") as file:
        write_benchmark_reports(file, benchmark_report)
        logger.info("analysis report file saved to path: %s", reports_filepath)


if __name__ == "__main__":
    try:
        logger.info("Starting analysis run")
        main()
    except Exception:
        logger.exception("Error running analysis")
    finally:
        logger.info("End analysis run")
