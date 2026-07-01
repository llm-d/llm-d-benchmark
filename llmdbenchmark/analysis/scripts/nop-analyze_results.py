#!/usr/bin/env python3

"""
Benchmark 'nop' analysis
"""
# pylint: disable=invalid-name

from datetime import datetime
import io
import os
import logging
from typing import Any
import pandas as pd
import yaml

from benchmark_report import BenchmarkReportV01 as BenchmarkReport  # pylint: disable=import-error
from benchmark_report.schema_v0_1 import Scenario  # pylint: disable=import-error

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


def fmt(val, spec):
    """format pandas report"""
    width = int(spec.split(".")[0])
    if pd.notna(val):
        return format(val, spec)
    return " " * width


def get_formatted_output(
    left_padding: int, columns: list[str], df: pd.DataFrame
) -> str:
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
    lines_padded = [" " * left_padding + s for s in lines]
    line = "\n".join(lines_padded)
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


def write_benchmark_scenario(file: io.TextIOWrapper, scenario: Scenario):
    """write benchmark scenario to file"""

    file.write("Scenario\n")
    file.write(f"  Deploy Methods       : {scenario.metadata['deploy_methods']}\n")
    file.write(f"  Harness              : {scenario.load.name}\n")
    file.write(f"  Load Format          : {scenario.metadata['load_format']}\n")
    file.write(f"  Sleep Mode On        : {scenario.metadata['sleep_mode']}\n")
    file.write(
        f"  Max Instances        : {scenario.metadata.get('max_instances', 'N/A')}\n"
    )
    file.write(f"  Model                : {scenario.model.name}\n")
    for engine in scenario.platform.engine:
        file.write("  Engine\n")
        file.write(f"    Name               : {engine.name}\n")
        file.write(f"    Image              : {engine.metadata['image']}\n")
        file.write(f"    Version            : {engine.version}\n")
        file.write(f"    Args               : {str(engine.args)}\n")
    for gpu in scenario.metadata["gpus"]:
        file.write("  GPU\n")
        file.write(f"    UUID               : {gpu['uuid']}\n")
        file.write(f"    Name               : {gpu['name']}\n")
        file.write(f"    Compute capability : {gpu['compute_cap']}\n")
        file.write(f"    Persistence Mode   : {gpu['persistence_mode']}\n")


def write_benchmark_reports(file: io.TextIOWrapper, benchmark_report: BenchmarkReport):
    """write benchmark reports to file"""

    scenario = benchmark_report.scenario
    write_benchmark_scenario(file, scenario)
    file.write("\n")

    time_iso = (
        datetime.fromtimestamp(benchmark_report.metrics.time.start)
        .astimezone()
        .isoformat()
    )
    duration = benchmark_report.metrics.time.duration

    file.write("Benchmark\n")
    file.write(f"  Start                             : {time_iso}\n")
    file.write(f"  Elapsed(secs)                     : {duration:7.3f}\n")

    left_padding = 4
    for metadata in benchmark_report.metrics.metadata:
        metadatas = metadata["value"]
        if metadata["name"] == "vllm_metrics":
            write_vllm_metrics(file, metadatas, left_padding)
        elif metadata["name"] == "extra_metrics":
            write_extra_metrics(file, metadatas)
        else:
            logger.info("Unhandled metrics name '%s'", metadata["name"])


def write_vllm_metrics(  # pylint: disable=too-many-locals,too-many-statements
    file: io.TextIOWrapper, metadatas: list[dict], left_padding: int
):
    """prints vLLM metrics"""

    for metrics_metadata in metadatas:
        name = metrics_metadata["name"]
        pod_start = metrics_metadata["pod_start"]["value"]
        vllm_start = (
            metrics_metadata["vllm_ready_timestamp"]["value"]
            - metrics_metadata["vllm_start_timestamp"]["value"]
            if metrics_metadata["vllm_ready_timestamp"]["value"]
            > metrics_metadata["vllm_start_timestamp"]["value"]
            else 0.0
        )
        elapsed = metrics_metadata["load"]["time"]["value"]
        rate = metrics_metadata["load"]["transfer_rate"]["value"]
        dynamo_bytecode_transform = metrics_metadata["dynamo_bytecode_transform"][
            "value"
        ]
        torch_compile = metrics_metadata["torch_compile"]["value"]
        initial_free = metrics_metadata["memory_profiling"]["initial_free"]["value"]
        after_free = metrics_metadata["memory_profiling"]["after_free"]["value"]
        profiling_time = metrics_metadata["memory_profiling"]["time"]["value"]
        load_cached_compiled_graph = metrics_metadata.get("load_cached_compiled_graph")
        compile_graph = metrics_metadata.get("compile_graph")

        file.write(f"\n  Name                              : {name}\n")
        file.write(f"    Pod  Start(secs)                : {pod_start:7.3f}\n")
        file.write(f"    vLLM Start(secs)                : {vllm_start:7.3f}\n")
        file.write("    Model Load\n")
        file.write(f"      Elapsed(secs)                 : {elapsed:7.3f}\n")
        file.write(f"      Rate(GiB/secs)                : {rate:7.3f}\n")
        file.write(
            f"    Dynamo Bytecode Transform(secs) : {dynamo_bytecode_transform:7.2f}\n"
        )
        if load_cached_compiled_graph is not None or compile_graph is not None:
            file.write("    Compiled Graph\n")
            if load_cached_compiled_graph is not None:
                file.write(
                    "      Load from Cache(secs)         : "
                    f"{load_cached_compiled_graph['value']:7.3f}\n"
                )
            if compile_graph is not None:
                file.write(
                    f"      Compile(secs)                 : {compile_graph['value']:7.3f}\n"
                )
        file.write(f"    Torch Compile(secs)             : {torch_compile:7.2f}\n")
        file.write("    Memory Profiling\n")
        file.write(f"      Elapsed(secs)                 : {profiling_time:7.2f}\n")
        file.write("      Free Memory GPU(GiB)\n")
        file.write(f"        Initial                     : {initial_free:7.2f}\n")
        file.write(f"        After                       : {after_free:7.2f}\n")

        metrics_sleep_wake = metrics_metadata.get("sleep_wake", [])
        if len(metrics_sleep_wake) > 0:
            file.write(
                "\n    After: Time elapsed after the previous vLLM sleep, wake, or ready state\n"
            )
            file.write(
                "    Elapsed: Time it took to transition to vLLM sleep or wait\n\n"
            )
            pandas_datas = []
            prev_timestamp = metrics_metadata["vllm_ready_timestamp"]["value"]
            for sleep_wake in metrics_sleep_wake:
                curr_timestamp = sleep_wake["timestamp"]["value"]
                diff = curr_timestamp - prev_timestamp if prev_timestamp > 0 else None
                prev_timestamp = curr_timestamp
                data = {
                    "Type": sleep_wake["type"],
                    "After(secs)": diff,
                    "Elapsed(secs)": sleep_wake["time"]["value"],
                    "GPU Freed(GiB)": None,
                    "GPU In Use(GiB)": None,
                }
                if sleep_wake["type"] == "sleep":
                    data["GPU Freed(GiB)"] = sleep_wake["gpu_freed"]["value"]
                    data["GPU In Use(GiB)"] = sleep_wake["gpu_in_use"]["value"]
                pandas_datas.append(data)

            df = pd.DataFrame(pandas_datas)
            file.write("\n")
            header = (
                f"{'Type':<6} "
                f"{'After(secs)':>13} "
                f"{'Elapsed(secs)':>13} "
                f"{'GPU Freed(GiB)':>15} "
                f"{'GPU In Use(GiB)':>15}"
            )
            file.write(f"{' ' * left_padding}{header}\n")
            file.write(f"{' ' * left_padding}{'-' * len(header)}\n")
            for _, r in df.iterrows():
                file.write(
                    f"{' ' * left_padding}"
                    f"{r['Type']:<6} "
                    f"{fmt(r['After(secs)'], '13.3f')} "
                    f"{fmt(r['Elapsed(secs)'], '13.3f')} "
                    f"{fmt(r['GPU Freed(GiB)'], '15.2f')} "
                    f"{fmt(r['GPU In Use(GiB)'], '15.2f')}\n"
                )
            file.write("\n")

        file.write("\n")
        categories = metrics_metadata.get("categories")
        if categories is not None and len(categories) > 0:
            data_frame = create_categories_dataframe(categories, 0, pd.DataFrame())
            file.write(
                get_formatted_output(left_padding, ["Category", "Process"], data_frame)
            )


def write_extra_metrics(file: io.TextIOWrapper, metadatas: list[dict]):
    """prints extra metrics"""

    for metrics_metadata in metadatas:
        if metrics_metadata["name"] == "fma":
            write_fma_metrics(file, metrics_metadata["iterations"], 0)
        else:
            logger.info("Unhandled extra metrics name '%s'", metrics_metadata["name"])


def write_fma_metrics(  # pylint: disable=too-many-locals,too-many-statements
    file: io.TextIOWrapper, iterations: list[dict], left_padding: int
):
    """prints FMA metrics"""

    file.write("\n\n")
    file.write("Actuation Conditions:\n")
    file.write("  T_cold_launcher: DPC creates new launcher + new vLLM instance\n")
    file.write("  T_warm: existing launcher creates new vLLM instance\n")
    file.write("  T_hot: waking sleeping vLLM instance\n\n")
    file.write("T_actuation: Time for the Requester Pod to be ready\n")
    file.write("T_hot: Hot-start timing\n")
    file.write("T_warm: Warm-start timing\n")
    file.write("T_cold_launcher: Cold-start-with-launcher timing\n")
    file.write(
        "Source: DPC = tighter timing from DPC logs, Kube = upper bound from Kube timestamps\n"
    )
    file.write("T_first_token: Time for vLLM server to return first token\n")
    file.write(
        "Each iteration scales the requester Deployment from 0 to 1 "
        "and then from 1 to 0\n"
    )

    hot_starts = 0
    warm_starts = 0
    cold_starts = 0
    total_iterations = len(iterations)
    pandas_datas = []
    for iteration in iterations:
        for launcher_info in iteration["launcher_infos"]:
            ct = float(launcher_info["requester_info"]["creation_timestamp"]["value"])
            rt = float(launcher_info["requester_info"]["ready_timestamp"]["value"])
            ttrr = rt - ct if rt > 0.0 else 0.0
            ttft = float(launcher_info["ttft"]["value"])
            actuation_condition = launcher_info["actuation_condition"]
            if actuation_condition == "T_hot":
                hot_starts += 1
            elif actuation_condition == "T_warm":
                warm_starts += 1
            elif actuation_condition == "T_cold_launcher":
                cold_starts += 1

            t_hot = launcher_info.get("t_wake")
            t_hot_val = (
                float(t_hot["value"])
                if isinstance(t_hot, dict) and "value" in t_hot
                else None
            )
            t_warm = launcher_info.get("t_instance_create")
            t_warm_val = (
                float(t_warm["value"])
                if isinstance(t_warm, dict) and "value" in t_warm
                else None
            )
            t_cold = launcher_info.get("t_cold_launcher")
            t_cold_val = (
                float(t_cold["value"])
                if isinstance(t_cold, dict) and "value" in t_cold
                else None
            )
            node = launcher_info.get("launcher_node", "")
            # Three-way timing source so a degraded pod-create baseline is
            # distinguishable from a container-start Kube fallback at a glance.
            # Fall back to the legacy dpc_timing_available flag for older reports
            # that predate the timing_source field.
            source_map = {
                "dpc": "DPC",
                "kube_container_start": "Kube (container-start)",
                "kube_pod_create": "Kube (pod-create)",
            }
            timing_source = launcher_info.get("timing_source")
            if timing_source in source_map:
                source = source_map[timing_source]
            else:
                source = (
                    "DPC"
                    if launcher_info.get("dpc_timing_available", False)
                    else "Kube"
                )

            pandas_datas.append(
                {
                    "Iteration": iteration["iteration"]["value"],
                    "vLLM Name": launcher_info["name"],
                    "Node": node,
                    "Actuation Condition": actuation_condition,
                    "T_actuation(s)": ttrr,
                    "T_hot(s)": t_hot_val,
                    "T_warm(s)": t_warm_val,
                    "T_cold(s)": t_cold_val,
                    "T_first_token(s)": ttft,
                    "Source": source,
                }
            )

    hot_hit_rate = hot_starts / total_iterations if total_iterations > 0 else 0.0
    warm_hit_rate = warm_starts / total_iterations if total_iterations > 0 else 0.0
    cold_hit_rate = cold_starts / total_iterations if total_iterations > 0 else 0.0

    df = pd.DataFrame(pandas_datas)

    file.write("\n")

    # Float formatting
    float_columns = [
        "T_actuation(s)",
        "T_hot(s)",
        "T_warm(s)",
        "T_cold(s)",
        "T_first_token(s)",
    ]

    # Compute column widths dynamically.
    # FMA per-path columns (T_hot, T_warm, T_cold) contain None for
    # inapplicable paths, so we handle NaN when computing widths.
    col_widths = {}
    for col in df.columns:
        if col in float_columns:
            max_float_width = max(
                df[col].apply(lambda x: len(f"{x:.4f}") if pd.notna(x) else 2)
            )
            col_widths[col] = max(len(col), max_float_width)
        else:
            col_widths[col] = max(len(col), df[col].astype(str).apply(len).max())

    space_between_cols = 2

    # Header
    header = (" " * space_between_cols).join(
        f"{col:<{col_widths[col]}}" for col in df.columns
    )
    file.write(f"{' ' * left_padding}{header}\n")

    # Separator
    separator = (" " * space_between_cols).join(
        "-" * col_widths[col] for col in df.columns
    )
    file.write(f"{' ' * left_padding}{separator}\n")

    # Rows -- per-path timing columns show "--" when not applicable
    for _, r in df.iterrows():
        row = []
        for col in df.columns:
            val = r[col]
            if col in float_columns:
                if pd.notna(val):
                    row.append(f"{val:>{col_widths[col]}.4f}")  # right-align numbers
                else:
                    row.append(f"{'--':>{col_widths[col]}}")  # N/A for this path
            elif isinstance(val, int):
                row.append(f"{val:>{col_widths[col]}}")
            else:
                row.append(f"{val:<{col_widths[col]}}")
        file.write(f"{' ' * left_padding}{(' ' * space_between_cols).join(row)}\n")

    file.write(f"\n  Hot_hit_rate:            {hot_hit_rate:.2f}\n")
    file.write(f"  Warm_hit_rate:           {warm_hit_rate:.2f}\n")
    file.write(f"  Cold_launcher_hit_rate:  {cold_hit_rate:.2f}\n")

    file.write("\n")


def main():
    """main entry point"""

    envs = get_env_variables(
        [
            "LLMDBENCH_CONTROL_WORK_DIR",
        ]
    )

    requests_dir = envs[0]

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
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Error running analysis")
    finally:
        logger.info("End analysis run")
