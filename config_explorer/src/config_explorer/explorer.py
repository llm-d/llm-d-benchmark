"""
This file contains function for configuration exploration using benchmarking
data from llm-d-benchmark.

The entrypoint is make_benchmark_runs_df() to initialize an empty Pandas
DataFrame which will store benchmark results, and add_benchmark_report_to_df()
to populate the DataFrame with data from a benchmark report file. The columns
in the DataFrame are described in the COLUMNS dictionary.

To assist with loading benchmark report files, get_benchmark_report_files() can
be used to find all benchmark report files within a search directory.

Once a DataFrame has been populated, analysis can proceed by selecting a set of
columns to be held constant during analysis. These columns should describe a
particular use case, such as the AI model, workload, and accelerator hardware.
For example, of we want to analyze throughput and latency performance of
prefill/decode disaggregated and aggregated setups, we may key together
['Model', 'GPU', 'ISL', 'OSL']. A unique set of values for these columns is
referred to as a "scenario". We can find what unique combinations of these
parameters exist within the dataset with get_scenarios(). If we are using a
text interface, such as a CLI or Jupyter notebook, we can use print_scenarios()
to view a table of scenarios available.

Upon selection of a particular scenario, we now need to choose another grouping
of columns which we will use to uniquely define a configuration. We can
describe disaggregated configurations with the columns
['P_Replicas', 'P_TP', 'D_Replicas', 'D_TP'], which will be our configuration
key.

Using our selected scenario and configuration key, we can begin plotting
metrics of interest using functions in plotting.py. We can also define
service-level objectives (SLOs) with the SLO class, creating a list of SLOs and
using get_meet_slo_df() to make a DataFrame of only rows that meet our SLOs.
We can use get_pareto_front_df() to find optimal configurations against pairs
of metrics, showing, for example, the tradeoff between throughput and latency.
"""

import builtins
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import pandas as pd

# TODO These packages can be imported in different ways depending on whether
# these are imported as a notebook, or installed as a config_explorer library
# in a Python environment. This needs to be made consistent as the overall
# llm-d-benchmark repository is refactored into full Python.
try:
    import convert
    import schema
    from constants import (
        BOUND_PREFIX_LEN,
        COLUMN_BOUND_STR,
        STR_TO_COLUMN_BOUND,
    )
except ImportError:
    from config_explorer import convert
    from config_explorer import schema
    from config_explorer.constants import (
        BOUND_PREFIX_LEN,
        COLUMN_BOUND_STR,
        STR_TO_COLUMN_BOUND,
    )


class Text:
    """ANSI SGR control codes for text formatting"""
    DEFAULT = "\x1b[0m"
    BOLD = "\x1b[1m"
    BOLD_OFF = "\x1b[22m"
    UNDERLINE = "\x1b[4m"
    UNDERLINE_OFF = "\x1b[24m"
    DEFAULT_COLOR = "\x1b[39m"
    DEFAULT_BG_COLOR = "\x1b[49m"
    RED = "\x1b[31m"
    YELLOW = "\x1b[33m"
    GREEN = "\x1b[32m"
    CYAN = "\x1b[36m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    BLACK = "\x1b[30m"
    WHITE = "\x1b[37m"
    BG_RED = "\x1b[41m"
    BG_YELLOW = "\x1b[43m"
    BG_GREEN = "\x1b[42m"
    BG_CYAN = "\x1b[46m"
    BG_BLUE = "\x1b[44m"
    BG_MAGENTA = "\x1b[45m"
    BG_BLACK = "\x1b[40m"
    BG_WHITE = "\x1b[47m"


class Pref:
    """Preferred direction for a metric."""

    # Lower is better
    LOW = -1
    # No preference
    NEUTRAL = 0
    # Higher is better
    HIGH = 1


@dataclass
class ColumnProperties:
    """Dataset column properties."""

    # String description of data type
    dtype: str
    # Label for plots and tables
    label: str
    # Preferred direction
    pref: Pref = Pref.NEUTRAL
    # Units
    units: str = ''

    def label_with_units(self) -> str:
        """
        Pretty print the label of the column with the units
        """

        if self.units == "":
            return self.label

        return f"{self.label} ({self.units})"


# Dataset columns about benchmark run
RUN_COLUMNS = {
    # Details about particular run
    'Directory': ColumnProperties(
        dtype='str',
        label='Directory',
    ),
    'Directory_Base': ColumnProperties(
        dtype='str',
        label='Base Directory'
    ),
    'Start': ColumnProperties(
        dtype='float',
        label='Start Time'
    ),
    'Duration': ColumnProperties(
        dtype='float',
        units='s',
        label='Duration',
    ),
}

# Dataset columns about configuration
CONFIGURATION_COLUMNS = {
    'Platform': ColumnProperties(
        dtype='str',
        label='Platform',
    ),
    # AI model name
    'Model': ColumnProperties(
        dtype='str',
        label='Model',
    ),
    # Accelerator and parallelism
    'GPU': ColumnProperties(
        dtype='str',
        label='Accelerator',
    ),
    'Num_GPUs': ColumnProperties(
        dtype='int',
        label='Number of GPUs',
        pref=Pref.LOW,
    ),
    'DP': ColumnProperties(
        dtype='int',
        label='DP',
    ),
    'TP': ColumnProperties(
        dtype='int',
        label='TP',
    ),
    'PP': ColumnProperties(
        dtype='int',
        label='PP',
    ),
    'EP': ColumnProperties(
        dtype='int',
        label='EP',
    ),
    'Replicas': ColumnProperties(
        dtype='int',
        label='Replicas',
    ),
    'P_DP': ColumnProperties(
        dtype='int',
        label='P DP',
    ),
    'P_TP': ColumnProperties(
        dtype='int',
        label='P TP',
    ),
    'P_PP': ColumnProperties(
        dtype='int',
        label='P PP',
    ),
    'P_EP': ColumnProperties(
        dtype='int',
        label='P EP',
    ),
    'P_Replicas': ColumnProperties(
        dtype='int',
        label='P Replicas',
    ),
    'D_DP': ColumnProperties(
        dtype='int',
        label='D DP',
    ),
    'D_TP': ColumnProperties(
        dtype='int',
        label='D TP',
    ),
    'D_PP': ColumnProperties(
        dtype='int',
        label='D PP',
    ),
    'D_EP': ColumnProperties(
        dtype='int',
        label='D EP',
    ),
    'D_Replicas': ColumnProperties(
        dtype='int',
        label='D Replicas',
    ),
    'Is_PD': ColumnProperties(
        dtype='bool',
        label='Is P/D',
    ),
    # Inference scheduler settings
    'KV_Cache_Scorer_Weight': ColumnProperties(
        dtype='float',
        label='KV Cache',
    ),
    'Queue_Scorer_Weight': ColumnProperties(
        dtype='float',
        label='Queue',
    ),
    'Prefix_Cache_Scorer_Weight': ColumnProperties(
        dtype='float',
        label='Prefix Cache',
    ),
    'Prefix_Cache_Scorer_Block_Size': ColumnProperties(
        dtype='int',
        label='Block Size',
    ),
    'Prefix_Cache_Scorer_LRU_Capacity_Per_Server': ColumnProperties(
        dtype='int',
        label='LRU/Server',
    ),
    'Prefix_Cache_Scorer_Max_Blocks_To_Match': ColumnProperties(
        dtype='int',
        label='Max Blocks',
    ),
    'Prefix_Cache_Scorer_Mode': ColumnProperties(
        dtype='bool',
        label='Prefix Mode',
    ),
    # Workload
    'Workload_Generator': ColumnProperties(
        dtype='str',
        label='Workload Generator',
    ),
    'ISL': ColumnProperties(
        dtype='int',
        label='Input Sequence Length',
    ),
    'OSL': ColumnProperties(
        dtype='int',
        label='Output Sequence Length',
    ),
    'Target_OSL': ColumnProperties(
        dtype='int',
        label='Target OSL',
    ),
    'Max_Concurrency': ColumnProperties(
        dtype='int',
        label='Concurrency',
    ),
    'Max_QPS': ColumnProperties(
        dtype='float',
        label='Request Rate',
        units='queries/s',
    ),
    'System_Prompt_Length': ColumnProperties(
        dtype='int',
        label='System Prompt Length',
    ),
    'Question_Length': ColumnProperties(
        dtype='int',
        label='Question Length',
    ),
    'Groups': ColumnProperties(
        dtype='int',
        label='Groups',
    ),
    'Prompts_Per_Group': ColumnProperties(
        dtype='int',
        label='Prompts per Group',
    ),
    # Requests
    'Total_Requests': ColumnProperties(
        dtype='int',
        label='Total Requests',
    ),
    'Failures': ColumnProperties(
        dtype='int',
        label='Failures',
    ),
}

# Dataset metrics columns
METRICS_COLUMNS = {
    # Performance metrics
    # Throughput
    'Request_Throughput': ColumnProperties(
        dtype='float',
        label='Request Throughput',
        pref=Pref.HIGH,
        units='req/s',
    ),
    'Output_Token_Throughput': ColumnProperties(
        dtype='float',
        label='Output Token Throughput',
        pref=Pref.HIGH,
        units='tok/s',
    ),
    'Total_Token_Throughput': ColumnProperties(
        dtype='float',
        label='Total Token Throughput',
        pref=Pref.HIGH,
        units='tok/s',
    ),
    'Thpt_per_GPU': ColumnProperties(
        dtype='float',
        label='Throughput per GPU',
        pref=Pref.HIGH,
        units='tok/s/GPU',
    ),
    'Thpt_per_User': ColumnProperties(
        dtype='float',
        label='Throughput per User',
        pref=Pref.HIGH,
        units='tok/s/user',
    ),
    # Latency
    # TTFT
    'Mean_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Mean Time to First Token',
        pref=Pref.LOW,
        units='ms',
    ),
    'StdDev_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token StDev',
        pref=Pref.LOW,
        units='ms',
    ),
    'Min_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Min Time to First Token',
        pref=Pref.LOW,
        units='ms',
    ),
    'P0.1_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P0.1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P1_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P5_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P5',
        pref=Pref.LOW,
        units='ms',
    ),
    'P10_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P10',
        pref=Pref.LOW,
        units='ms',
    ),
    'P25_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P25',
        pref=Pref.LOW,
        units='ms',
    ),
    'P50_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P50',
        pref=Pref.LOW,
        units='ms',
    ),
    'P75_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P75',
        pref=Pref.LOW,
        units='ms',
    ),
    'P90_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P90',
        pref=Pref.LOW,
        units='ms',
    ),
    'P95_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P95',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P99',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99.9_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Time to First Token P99.9',
        pref=Pref.LOW,
        units='ms',
    ),
    'Max_TTFT_ms': ColumnProperties(
        dtype='float',
        label='Max Time to First Token',
        pref=Pref.LOW,
        units='ms',
    ),
    # TPOT
    'Mean_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Mean Time per Output Token',
        pref=Pref.LOW,
        units='ms',
    ),
    'StdDev_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token StdDev',
        pref=Pref.LOW,
        units='ms',
    ),
    'Min_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Min Time per Output Token',
        pref=Pref.LOW,
        units='ms',
    ),
    'P0.1_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P0.1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P1_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P5_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P5',
        pref=Pref.LOW,
        units='ms',
    ),
    'P10_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P10',
        pref=Pref.LOW,
        units='ms',
    ),
    'P25_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P25',
        pref=Pref.LOW,
        units='ms',
    ),
    'P50_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P50',
        pref=Pref.LOW,
        units='ms',
    ),
    'P75_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P75',
        pref=Pref.LOW,
        units='ms',
    ),
    'P90_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P90',
        pref=Pref.LOW,
        units='ms',
    ),
    'P95_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P95',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P99',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99.9_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Time per Output Token P99.9',
        pref=Pref.LOW,
        units='ms',
    ),
    'Max_TPOT_ms': ColumnProperties(
        dtype='float',
        label='Max Time per Output Token',
        pref=Pref.LOW,
        units='ms',
    ),
    # ITL
    'Mean_ITL_ms': ColumnProperties(
        dtype='float',
        label='Mean Inter-Token Latency',
        pref=Pref.LOW,
        units='ms',
    ),
    'StdDev_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency StdDev',
        pref=Pref.LOW,
        units='ms',
    ),
    'Min_ITL_ms': ColumnProperties(
        dtype='float',
        label='Min Inter-Token Latency',
        pref=Pref.LOW,
        units='ms',
    ),
    'P0.1_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P0.1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P1_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P5_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P5',
        pref=Pref.LOW,
        units='ms',
    ),
    'P10_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P10',
        pref=Pref.LOW,
        units='ms',
    ),
    'P25_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P25',
        pref=Pref.LOW,
        units='ms',
    ),
    'P50_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P50',
        pref=Pref.LOW,
        units='ms',
    ),
    'P75_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P75',
        pref=Pref.LOW,
        units='ms',
    ),
    'P90_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P90',
        pref=Pref.LOW,
        units='ms',
    ),
    'P95_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P95',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P99',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99.9_ITL_ms': ColumnProperties(
        dtype='float',
        label='Inter-Token Latency P99.9',
        pref=Pref.LOW,
        units='ms',
    ),
    'Max_ITL_ms': ColumnProperties(
        dtype='float',
        label='Max Inter-Token Latency',
        pref=Pref.LOW,
        units='ms',
    ),
    # E2EL
    'Mean_E2EL_ms': ColumnProperties(
        dtype='float',
        label='Mean End-to-End Latency',
        pref=Pref.LOW,
        units='ms',
    ),
    'StdDev_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency StdDev',
        pref=Pref.LOW,
        units='ms',
    ),
    'Min_E2EL_ms': ColumnProperties(
        dtype='float',
        label='Min End-to-End Latency',
        pref=Pref.LOW,
        units='ms',
    ),
    'P0.1_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P0.1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P1_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P1',
        pref=Pref.LOW,
        units='ms',
    ),
    'P5_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P5',
        pref=Pref.LOW,
        units='ms',
    ),
    'P10_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P10',
        pref=Pref.LOW,
        units='ms',
    ),
    'P25_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P25',
        pref=Pref.LOW,
        units='ms',
    ),
    'P50_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P50',
        pref=Pref.LOW,
        units='ms',
    ),
    'P75_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P75',
        pref=Pref.LOW,
        units='ms',
    ),
    'P90_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P90',
        pref=Pref.LOW,
        units='ms',
    ),
    'P95_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P95',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P99',
        pref=Pref.LOW,
        units='ms',
    ),
    'P99.9_E2EL_ms': ColumnProperties(
        dtype='float',
        label='End-to-End Latency P99.9',
        pref=Pref.LOW,
        units='ms',
    ),
    'Max_E2EL_ms': ColumnProperties(
        dtype='float',
        label='Max End-to-End Latency',
        pref=Pref.LOW,
        units='ms',
    ),
}

# Non-metrics columns, which may be used as an independent input variable
INPUT_COLUMNS = {}
INPUT_COLUMNS.update(RUN_COLUMNS)
INPUT_COLUMNS.update(CONFIGURATION_COLUMNS)

# All dataset columns
COLUMNS = {}
COLUMNS.update(RUN_COLUMNS)
COLUMNS.update(CONFIGURATION_COLUMNS)
COLUMNS.update(METRICS_COLUMNS)


@dataclass
class SLO:
    """Service level objective."""

    # Column of metric associated with the SLO
    col: str
    # Value the metric must be no worse than
    value: float

    def __post_init__(self):
        if self.col not in COLUMNS:
            raise ValueError(f'Column does not exist: {self.col}')
        if COLUMNS[self.col].dtype != 'float':
            raise TypeError(f'Column must have float datatype: {self.col}')
        if COLUMNS[self.col].pref == Pref.NEUTRAL:
            raise Exception(
                f'Column must have a preferred direction: {
                    self.col}')


def col_base(col: str) -> str:
    """Get original column name, removing bound prefixes if present.

    Args:
        col (str): Column name, which may include a bound prefix.

    Returns:
        str: Column name, without any bound prefixes.
    """
    if col[:BOUND_PREFIX_LEN] in COLUMN_BOUND_STR:
        return col[BOUND_PREFIX_LEN:]
    return col


def check_dir(dir: str) -> None:
    """Print an error if directory does not exist.

    Args:
        dir (str): Directory to check existence of.
    """
    if not os.path.isdir(dir):
        raise Exception(f'Invalid path: {dir}')


def check_file(file: str) -> None:
    """Print an error if file does not exist.

    Args:
        file (str): File to check existence of.
    """
    if not os.path.isfile(file):
        raise Exception(f'Invalid file: {file}')


def get_nested(ndict: dict[Any, Any], path: list[Any],
               default: Any = None) -> Any:
    """Get value from path through nested dicts.

    Args:
        d (dict): Nested dict to get value from.
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


def mul(a: int | None, b: int | None) -> int | None:
    """Multiply two values, returning None if either value is None.

    Args:
        a (int | None): First multiplicand.
        b (int | None): Second multiplicand.

    Returns:
        int | None: Multiplied result if multiplicands exist, otherwise None.
    """
    if a and b:
        return a * b
    return None


def get_benchmark_report_files(source_dir: str) -> list[str]:
    """Get a list of benchmark report files within provided path (recursive).

    Args:
        source_dir (str): Directory to recursively search for results files.

    Returns:
        list: List of paths to benchmark report files.
    """
    rb_files = []
    check_dir(source_dir)
    path = Path(source_dir)
    for file in path.rglob('benchmark_report,_*.yaml', recurse_symlinks=True):
        rb_files.append(str(file))
    return rb_files


def make_benchmark_runs_df() -> pd.DataFrame:
    """Create DataFrame for benchmark run results.

    Returns:
        DataFrame: Empty DataFrame for benchmark runs.
    """
    schema = {}
    for col, props in COLUMNS.items():
        schema[col] = pd.Series(dtype=props.dtype)
    return pd.DataFrame(schema)


def _get_replicas_and_parallelism(
        report: schema.BenchmarkReport) -> dict[str, int | None]:
    """Get the number of replicas and parallelisms.

    Args:
        report (BenchmarkReport): Benchmark run to evaluate.

    Returns:
        dict[str, int | None]: Replicas and parallelisms for standalone or
            prefill/decode configuration. Irrelevant fields will have a value
            of None.
    """
    rp = {
        'replicas': report.scenario.host.type.count(schema.HostType.REPLICA),
        'tp': None,
        'dp': None,
        'pp': None,
        'ep': None,
        'p_replicas': report.scenario.host.type.count(schema.HostType.PREFILL),
        'p_tp': None,
        'p_dp': None,
        'p_pp': None,
        'p_ep': None,
        'd_replicas': report.scenario.host.type.count(schema.HostType.DECODE),
        'd_tp': None,
        'd_dp': None,
        'd_pp': None,
        'd_ep': None,
    }
    if rp['replicas'] == 0:
        rp['replicas'] = None
    if rp['p_replicas'] == 0:
        rp['p_replicas'] = None
    if rp['d_replicas'] == 0:
        rp['d_replicas'] = None

    if rp['replicas']:
        # We have an aggregate setup
        rp['is_pd'] = False
        rp['tp'] = report.scenario.host.accelerator[0].parallelism.tp
        rp['dp'] = report.scenario.host.accelerator[0].parallelism.dp
        rp['pp'] = report.scenario.host.accelerator[0].parallelism.pp
        rp['ep'] = report.scenario.host.accelerator[0].parallelism.ep
        return rp
    # We have a P/D setup
    rp['is_pd'] = True
    for ii, accel in enumerate(report.scenario.host.accelerator):
        if report.scenario.host.type[ii] is schema.HostType.PREFILL and rp['p_tp'] is None:
            rp['p_tp'] = accel.parallelism.tp
            rp['p_dp'] = accel.parallelism.dp
            rp['p_pp'] = accel.parallelism.pp
            rp['p_ep'] = accel.parallelism.ep
        if report.scenario.host.type[ii] is schema.HostType.DECODE and rp['d_tp'] is None:
            rp['d_tp'] = accel.parallelism.tp
            rp['d_dp'] = accel.parallelism.dp
            rp['d_pp'] = accel.parallelism.pp
            rp['d_ep'] = accel.parallelism.ep
        if rp['p_tp'] and rp['d_tp']:
            break
    return rp


def add_benchmark_report_to_df(
        runs_df: pd.DataFrame,
        br_file: str) -> None:
    """Load a results file and add it to the DataFrame of benchmark runs.

    Args:
        runs_df (DataFrame): DataFrame to add a row to for the provided run.
        br_file (str): Benchmark report file to import.
    """
    report = convert.import_benchmark_report(br_file)

    # Get plugin parameters
    prefix_cache_scorer_block_size = None
    prefix_cache_scorer_lur_capacity_per_server = None
    prefix_cache_scorer_max_blocks_to_match = None
    prefix_cache_scorer_mode = ''
    if report.scenario.platform.metadata and isinstance(
            report.scenario.platform.metadata, dict):
        for plugin in get_nested(
            report.scenario.platform.metadata, [
                'inferenceScheduler', 'plugins'], []):
            if plugin.get('type') == 'prefix-cache-scorer':
                if 'parameters' not in plugin:
                    continue
                prefix_cache_scorer_block_size = plugin['parameters'].get(
                    'blockSize', 16)
                prefix_cache_scorer_lur_capacity_per_server = plugin['parameters'].get(
                    'lruCapacityPerServer', 31250)
                prefix_cache_scorer_max_blocks_to_match = plugin['parameters'].get(
                    'maxPrefixBlocksToMatch', 256)
                # If mode is 'cache_tracking', then precise prefix scoring is
                # used
                prefix_cache_scorer_mode = plugin['parameters'].get(
                    'mode', 'default')

    # Set default weights to zero (disabled)
    # TODO: capture other settings for prefix cache scorer
    # https://gateway-api-inference-extension.sigs.k8s.io/guides/epp-configuration/prefix-aware/
    prefix_cache_scorer_weight = 0
    kv_cache_scorer_weight = 0
    queue_scorer_weight = 0
    # TODO: this analysis assumes only a single scheduling profile.
    # In addition we assume the plugins have not been renamed, and the pluginRef
    # is the same as the plugin type.
    # https://gateway-api-inference-extension.sigs.k8s.io/guides/epp-configuration/config-text/
    if report.scenario.platform.metadata and isinstance(
            report.scenario.platform.metadata, dict):
        plugins = get_nested(
            report.scenario.platform.metadata, [
                'inferenceScheduler', 'schedulingProfiles'], [
                {}])[0].get(
            'plugins', [])
        for plugin in plugins:
            if plugin.get('pluginRef') == 'prefix-cache-scorer':
                prefix_cache_scorer_weight = plugin.get('weight', 1)
            if plugin.get('pluginRef') == 'kv-cache-scorer':
                kv_cache_scorer_weight = plugin.get('weight', 1)
            if plugin.get('pluginRef') == 'queue-scorer':
                queue_scorer_weight = plugin.get('weight', 1)

    # TODO getting concurrency is specific to each harness, will need
    # a way to capture this universally in the report so we don't have to do
    # specific extractions like this.
    if report.scenario.load.name == schema.WorkloadGenerator.VLLM_BENCHMARK:
        concurrency = report.scenario.load.args.get('max_concurrency')
    elif report.scenario.load.name == schema.WorkloadGenerator.GUIDELLM:
        concurrency = get_nested(
            report.scenario.load.args, ['profile', 'measured_concurrencies'])
        if concurrency:
            concurrency = concurrency[0]
    else:
        concurrency = None

    max_qps = None
    if report.scenario.load.name == schema.WorkloadGenerator.INFERENCE_PERF:
        # Workload generator stage
        stage = report.scenario.load.metadata.get('stage')
        if stage is not None:
            stage_list = get_nested(
                report.scenario.load.args, [
                    'load', 'stages'])
            max_qps = stage_list[stage].get('rate')

    rp = _get_replicas_and_parallelism(report)
    if rp['is_pd']:
        num_gpus = 0
        # We assume that EP = TP, where EP is used on expert layers, so no
        # need to add EP into the GPU count.
        if rp['p_replicas']:
            num_gpus += rp['p_tp'] * rp['p_dp'] * rp['p_pp'] * rp['p_replicas']
        if rp['d_replicas']:
            num_gpus += rp['d_tp'] * rp['d_dp'] * rp['d_pp'] * rp['d_replicas']
    else:
        num_gpus = rp['tp'] * rp['replicas']

    thpt_per_gpu = report.metrics.throughput.output_tokens_per_sec / num_gpus
    if concurrency:
        thpt_per_user = report.metrics.throughput.output_tokens_per_sec / concurrency
    else:
        thpt_per_user = None

    # Multipliers to ensure values are in ms
    ttft_mult = 1000 if report.metrics.latency.time_to_first_token.units == schema.Units.S else 1
    tpot_mult = 1000 if report.metrics.latency.time_per_output_token.units == schema.Units.S_PER_TOKEN else 1
    itl_mult = 1000 if report.metrics.latency.inter_token_latency.units == schema.Units.S_PER_TOKEN else 1
    e2el_mult = 1000 if report.metrics.latency.request_latency.units == schema.Units.S else 1

    # Add row to DataFrame
    runs_df.loc[len(runs_df)] = {
        # Details about particular run
        'Directory': os.path.abspath(br_file).rsplit(os.sep, 1)[0],
        'Directory_Base': os.path.abspath(br_file).rsplit(os.sep, 2)[0],
        'Start': report.metrics.time.start,
        'Duration': report.metrics.time.duration,
        'Platform': report.scenario.platform.engine[0].name,
        # AI model name
        'Model': report.scenario.model.name,
        # Accelerator and parallelism
        # Assume only a single GPU type
        'GPU': report.scenario.host.accelerator[0].model,
        'Num_GPUs': num_gpus,
        'DP': rp['dp'],
        'TP': rp['tp'],
        'PP': rp['pp'],
        'EP': rp['ep'],
        'Replicas': rp['replicas'],
        'P_DP': rp['p_dp'],
        'P_TP': rp['p_tp'],
        'P_PP': rp['p_pp'],
        'P_EP': rp['p_ep'],
        'P_Replicas': rp['p_replicas'],
        'D_DP': rp['d_dp'],
        'D_TP': rp['d_tp'],
        'D_PP': rp['d_pp'],
        'D_EP': rp['d_ep'],
        'D_Replicas': rp['d_replicas'],
        'Is_PD': rp['is_pd'],
        # Inference scheduler settings
        'KV_Cache_Scorer_Weight': kv_cache_scorer_weight,
        'Queue_Scorer_Weight': queue_scorer_weight,
        'Prefix_Cache_Scorer_Weight': prefix_cache_scorer_weight,
        'Prefix_Cache_Scorer_Block_Size': prefix_cache_scorer_block_size,
        'Prefix_Cache_Scorer_LRU_Capacity_Per_Server': prefix_cache_scorer_lur_capacity_per_server,
        'Prefix_Cache_Scorer_Max_Blocks_To_Match': prefix_cache_scorer_max_blocks_to_match,
        'Prefix_Cache_Scorer_Mode': prefix_cache_scorer_mode,
        # Workload
        'Workload_Generator': report.scenario.load.name,
        'ISL': int(round(report.metrics.requests.input_length.mean)),
        'OSL': int(round(report.metrics.requests.output_length.mean)),
        'Target_OSL': int(get_nested(report.scenario.load.args, ['data', 'shared_prefix', 'output_len'], -1)),
        'Max_Concurrency': concurrency,
        'Max_QPS': max_qps,
        'System_Prompt_Length': get_nested(report.scenario.load.args, ['data', 'shared_prefix', 'system_prompt_len']),
        'Question_Length': get_nested(report.scenario.load.args, ['data', 'shared_prefix', 'question_len']),
        'Groups': get_nested(report.scenario.load.args, ['data', 'shared_prefix', 'num_groups']),
        'Prompts_Per_Group': get_nested(report.scenario.load.args, ['data', 'shared_prefix', 'num_prompts_per_group']),
        # Requests
        'Total_Requests': report.metrics.requests.total,
        'Failures': report.metrics.requests.failures,
        # Performance metrics
        # Throughput
        'Request_Throughput': report.metrics.throughput.requests_per_sec,
        'Output_Token_Throughput': report.metrics.throughput.output_tokens_per_sec,
        'Total_Token_Throughput': report.metrics.throughput.total_tokens_per_sec,
        'Thpt_per_GPU': thpt_per_gpu,
        'Thpt_per_User': thpt_per_user,
        # Latency
        # TTFT
        'Mean_TTFT_ms': mul(report.metrics.latency.time_to_first_token.mean, ttft_mult),
        'StdDev_TTFT_ms': mul(report.metrics.latency.time_to_first_token.stddev, ttft_mult),
        'Min_TTFT_ms': mul(report.metrics.latency.time_to_first_token.min, ttft_mult),
        'P0.1_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p0p1, ttft_mult),
        'P1_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p1, ttft_mult),
        'P5_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p5, ttft_mult),
        'P10_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p10, ttft_mult),
        'P25_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p25, ttft_mult),
        'P50_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p50, ttft_mult),
        'P75_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p75, ttft_mult),
        'P90_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p90, ttft_mult),
        'P95_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p95, ttft_mult),
        'P99_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p99, ttft_mult),
        'P99.9_TTFT_ms': mul(report.metrics.latency.time_to_first_token.p99p9, ttft_mult),
        'Max_TTFT_ms': mul(report.metrics.latency.time_to_first_token.max, ttft_mult),
        # TPOT
        'Mean_TPOT_ms': mul(report.metrics.latency.time_per_output_token.mean, tpot_mult),
        'StdDev_TPOT_ms': mul(report.metrics.latency.time_per_output_token.stddev, tpot_mult),
        'Min_TPOT_ms': mul(report.metrics.latency.time_per_output_token.min, tpot_mult),
        'P0.1_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p0p1, tpot_mult),
        'P1_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p1, tpot_mult),
        'P5_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p5, tpot_mult),
        'P10_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p10, tpot_mult),
        'P25_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p25, tpot_mult),
        'P50_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p50, tpot_mult),
        'P75_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p75, tpot_mult),
        'P90_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p90, tpot_mult),
        'P95_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p95, tpot_mult),
        'P99_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p99, tpot_mult),
        'P99.9_TPOT_ms': mul(report.metrics.latency.time_per_output_token.p99p9, tpot_mult),
        'Max_TPOT_ms': mul(report.metrics.latency.time_per_output_token.max, tpot_mult),
        # ITL
        'Mean_ITL_ms': mul(report.metrics.latency.inter_token_latency.mean, itl_mult),
        'StdDev_ITL_ms': mul(report.metrics.latency.inter_token_latency.stddev, itl_mult),
        'Min_ITL_ms': mul(report.metrics.latency.inter_token_latency.min, itl_mult),
        'P0.1_ITL_ms': mul(report.metrics.latency.inter_token_latency.p0p1, itl_mult),
        'P1_ITL_ms': mul(report.metrics.latency.inter_token_latency.p1, itl_mult),
        'P5_ITL_ms': mul(report.metrics.latency.inter_token_latency.p5, itl_mult),
        'P10_ITL_ms': mul(report.metrics.latency.inter_token_latency.p10, itl_mult),
        'P25_ITL_ms': mul(report.metrics.latency.inter_token_latency.p25, itl_mult),
        'P50_ITL_ms': mul(report.metrics.latency.inter_token_latency.p50, itl_mult),
        'P75_ITL_ms': mul(report.metrics.latency.inter_token_latency.p75, itl_mult),
        'P90_ITL_ms': mul(report.metrics.latency.inter_token_latency.p90, itl_mult),
        'P95_ITL_ms': mul(report.metrics.latency.inter_token_latency.p95, itl_mult),
        'P99_ITL_ms': mul(report.metrics.latency.inter_token_latency.p99, itl_mult),
        'P99.9_ITL_ms': mul(report.metrics.latency.inter_token_latency.p99p9, itl_mult),
        'Max_ITL_ms': mul(report.metrics.latency.inter_token_latency.max, itl_mult),
        # E2EL
        'Mean_E2EL_ms': mul(report.metrics.latency.request_latency.mean, e2el_mult),
        'StdDev_E2EL_ms': mul(report.metrics.latency.request_latency.stddev, e2el_mult),
        'Min_E2EL_ms': mul(report.metrics.latency.request_latency.min, e2el_mult),
        'P0.1_E2EL_ms': mul(report.metrics.latency.request_latency.p0p1, e2el_mult),
        'P1_E2EL_ms': mul(report.metrics.latency.request_latency.p1, e2el_mult),
        'P5_E2EL_ms': mul(report.metrics.latency.request_latency.p5, e2el_mult),
        'P10_E2EL_ms': mul(report.metrics.latency.request_latency.p10, e2el_mult),
        'P25_E2EL_ms': mul(report.metrics.latency.request_latency.p25, e2el_mult),
        'P50_E2EL_ms': mul(report.metrics.latency.request_latency.p50, e2el_mult),
        'P75_E2EL_ms': mul(report.metrics.latency.request_latency.p75, e2el_mult),
        'P90_E2EL_ms': mul(report.metrics.latency.request_latency.p90, e2el_mult),
        'P95_E2EL_ms': mul(report.metrics.latency.request_latency.p95, e2el_mult),
        'P99_E2EL_ms': mul(report.metrics.latency.request_latency.p99, e2el_mult),
        'P99.9_E2EL_ms': mul(report.metrics.latency.request_latency.p99p9, e2el_mult),
        'Max_E2EL_ms': mul(report.metrics.latency.request_latency.max, e2el_mult),
    }


def get_scenarios(
        runs_df: pd.DataFrame,
        scenario_columns: list[str],
        bounded: bool = False) -> list[dict[str, Any]]:
    """Get a list of available scenarios and numeric bounds from runs DataFrame.

    Args:
        runs_df (DataFrame): Benchmark runs to find the scenarios for.
        scenario_columns (list[str]): Columns to group into common sets.
        bounded (bool): For numeric columns, return min/max bounds.

    Returns:
        list[dict[str, Any]]: List of scenarios, consisting of unique groups of
            values from scenario_columns. When bounded scenarios are returned,
            any numeric columns are given as the min/max available with
            __ge__ and __le__ prefixes, respectively.
    """
    # Non-numeric columns
    cols_nn = []
    # Numeric columns
    cols_num = []
    for col in scenario_columns:
        if col not in runs_df.columns:
            raise KeyError(f'Invalid column: {col}')
        if COLUMNS[col].dtype in ['int', 'float']:
            cols_num.append(col)
        else:
            cols_nn.append(col)

    # Get unique combinations of values for non-numeric scenario columns,
    # as tuples.
    if bounded:
        if not cols_nn:
            raise Exception(
                'Scenario must include at least one non-numeric column')
        scenario_tuples = list(set(runs_df.set_index(cols_nn).index.dropna()))
    else:
        scenario_tuples = list(
            set(runs_df.set_index(scenario_columns).index.dropna()))

    # Create list of scenario dicts
    scenarios = []
    # If there is a column that is all NA in a scenario, we will drop that
    # scenario
    all_na = False
    for s_tuple in scenario_tuples:
        s_dict = {}
        if bounded:
            for ii, col_nn in enumerate(cols_nn):
                s_dict[col_nn] = s_tuple[ii]
            # Get rows matching this scenario's non-numeric columns
            df = get_scenario_df(runs_df, s_dict)
            # Get min/max for numeric columns of this scenario
            for col_num in cols_num:
                if df[col_num].isna().all():
                    # This scenario has a column that is all NA, drop it
                    all_na = True
                    break
                # Format as appropriate data type
                fmt = getattr(builtins, COLUMNS[col_num].dtype)
                # Get min/max
                val_min = fmt(df[col_num].min())
                val_max = fmt(df[col_num].max())
                if val_min == val_max:
                    # Column only has a single value, no need to specify bounds
                    s_dict[col_num] = val_min
                else:
                    s_dict['__ge__' + col_num] = val_min
                    s_dict['__le__' + col_num] = val_max
        else:
            for ii, col in enumerate(scenario_columns):
                s_dict[col] = s_tuple[ii]
        if not all_na:
            # Add scenario only if there are rows were all columns have data
            scenarios.append(s_dict)
        all_na = False

    return scenarios


def get_scenario_df(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any]) -> pd.DataFrame:
    """Get rows from a dataframe matching a scenario.

    Args:
        runs_df (pandas.DataFrame): Benchmark runs to retrieve the
            scenario data from.
        scenario (dict[str, Any]): Columns and values to match.

    Returns:
        pandas.DataFrame: Rows matching the scenario.
    """
    for col, val in scenario.items():
        if col[:BOUND_PREFIX_LEN] == '__ge__':
            runs_df = runs_df[(runs_df[col[BOUND_PREFIX_LEN:]] >= val)]
        elif col[:BOUND_PREFIX_LEN] == '__gt__':
            runs_df = runs_df[(runs_df[col[BOUND_PREFIX_LEN:]] > val)]
        elif col[:BOUND_PREFIX_LEN] == '__lt__':
            runs_df = runs_df[(runs_df[col[BOUND_PREFIX_LEN:]] < val)]
        elif col[:BOUND_PREFIX_LEN] == '__le__':
            runs_df = runs_df[(runs_df[col[BOUND_PREFIX_LEN:]] <= val)]
        else:
            runs_df = runs_df[(runs_df[col] == val)]
    return runs_df


def set_scenario_bounds(
        scenario: dict[str, Any],
        bounds: dict[str, dict[str, int | float]]) -> dict[str, Any]:
    """Create a new scenario with bounds applied.

    Args:
        scenario (dict[str, Any]): Scenario to apply new bounds to.
        bounds (dict[str, dict[str, int | float]]): Bounds to apply to
            scenario.

    Returns:
        dict[str, Any]: Scenario with updated column bounds.
    """

    scenario_bounded = {}

    # Get scenario columns, without bound prefixes
    scenario_cols = []
    for col in scenario:
        cb = col_base(col)
        if cb not in scenario_cols:
            scenario_cols.append(cb)
    # Make sure bounds apply only to columns that exist in scenario
    for col in bounds:
        if col not in scenario_cols:
            raise KeyError(f'Invalid column for scenario: {col_base(col)}')

    # Add columns not in bounds to scenario_bounded
    for col, val in scenario.items():
        if col_base(col) in bounds:
            continue
        scenario_bounded[col] = val

    # Add new bounds to scenario
    for col, bdict in bounds.items():
        if not bdict:
            raise Exception(f'Empty bounds for column: {col}')
        for bb, val in bdict.items():
            if bb in STR_TO_COLUMN_BOUND:
                scenario_bounded[STR_TO_COLUMN_BOUND[bb] + col] = val
            else:
                raise Exception(f'Invalid bound type: {bb}')

    return scenario_bounded


def rebound_scenario(
        runs_df: pd.DataFrame,
        scenario: dict[str, Any]) -> dict[str, Any]:
    """Update scenario bounds to match available data.

    Tighten any bounds that loosely describe available data.

    For bounds on a column which result in a single value, remove bounds and
    set this to an inequality.

    If there is no data matching the scenario, return scenario as-is.

    Args:
        runs_df (pandas.DataFrame): Benchmark runs the scenario applies to.
        scenario (dict[str, Any]): Columns and values to match.

    Returns:
        dict[str, Any]: Scenario with updated column bounds.
    """

    df = get_scenario_df(runs_df, scenario)
    if len(df) == 0:
        return scenario

    # Columns that are given as a bound
    cols_bounded = []
    # Get columns that are bounded along with their min/max values available
    scenario_tight = {}
    for col, val in scenario.items():
        if col[:BOUND_PREFIX_LEN] in COLUMN_BOUND_STR:
            bcol = col_base(col)
            if bcol not in cols_bounded:
                # Keep record of bounded columns we already covered
                cols_bounded.append(bcol)
                # Format as appropriate data type
                fmt = getattr(builtins, COLUMNS[bcol].dtype)
                # Get min/max
                val_min = fmt(df[bcol].min())
                val_max = fmt(df[bcol].max())
                if val_min == val_max:
                    # Column only has a single value, no need to specify bounds
                    scenario_tight[bcol] = val_min
                else:
                    # Apply lower and upper bounds matching available data
                    scenario_tight['__ge__' + bcol] = val_min
                    scenario_tight['__le__' + bcol] = val_max
        else:
            # Fixed column
            scenario_tight[col] = val

    return scenario_tight


def get_scenario_counts(
    runs_df: pd.DataFrame,
    scenarios: list[dict[str, Any]],
) -> list[int]:
    """Get a count of rows in DataFrame matching each scenario.

    Args:
        runs_df (pandas.DataFrame): Benchmark runs to count scenario rows from.
        scenarios (list[dict[str, Any]]): Scenario groups to count.

    Returns:
        list[int]: Counts for each scenario.
    """
    counts = []
    for sc in scenarios:
        count = len(get_scenario_df(runs_df, sc))
        counts.append(count)
    return counts


def print_scenarios(
    scenarios: list[dict[str, Any]],
    runs_df: pd.DataFrame | None = None,
    min_count: int = 0
) -> None:
    """Print a formatted table of scenarios.

    Args:
        scenarios (list[dict[str, Any]]): Scenario groups to print.
        runs_df (pandas.DataFrame | None): Benchmark runs to retrieve the
            scenario data from.
        min_count (int): Only show scenarios with at least this many rows.
    """

    if not scenarios:
        print(f'{Text.BOLD}{Text.RED}No scenarios available!{Text.DEFAULT}')
        return

    col_names = []
    # Length of column headers in printable characters
    col_names_len = []
    for col in scenarios[0].keys():

        if col[:BOUND_PREFIX_LEN] in COLUMN_BOUND_STR:
            col_bound = col[:BOUND_PREFIX_LEN]
            col_base = col[BOUND_PREFIX_LEN:]
            col_names.append(
                col_base +
                Text.MAGENTA +
                COLUMN_BOUND_STR[col_bound] +
                Text.DEFAULT +
                Text.BOLD)
            col_names_len.append(len(col[BOUND_PREFIX_LEN:]) + 1)
        else:
            col_names.append(col)
            col_names_len.append(len(col))

    # Get maximum text length for each column, including header
    spans = col_names_len[:]
    for sc in scenarios:
        for ii, value in enumerate(sc.values()):
            if spans[ii] < len(str(value)):
                spans[ii] = len(str(value))

    # Create header, starting with scenario index
    if runs_df is None:
        header = f'{Text.BOLD}{Text.BLUE}IDX  {Text.DEFAULT}{Text.BOLD}'
    else:
        counts = get_scenario_counts(runs_df, scenarios)
        header = f'{
            Text.BOLD}{
            Text.BLUE}IDX  {
            Text.RED}Count  {
                Text.DEFAULT}{
                    Text.BOLD}'

    # Add each column name to header
    for ii, col in enumerate(col_names):
        header += col + " " * (spans[ii] - col_names_len[ii] + 2)
    header += f'{Text.DEFAULT}'
    print(header)

    # Print details of each scenario
    for ii, sc in enumerate(scenarios):
        row = f'{Text.BLUE}{ii}{Text.DEFAULT}' + " " * (5 - len(str(ii)))
        if counts:
            if counts[ii] < min_count:
                continue
            row += f'{Text.RED}{counts[ii]}{Text.DEFAULT}' + \
                " " * (7 - len(str(counts[ii])))
        for jj, val in enumerate(sc.values()):
            row += f'{str(val)}' + " " * (spans[jj] - len(str(val)) + 2)
        print(row)


def make_scenarios_summary_df(
    scenarios: list[dict[str, Any]],
    runs_df: pd.DataFrame,
    min_count: int = 0
) -> pd.DataFrame:
    """
    Make a DataFrame of schenarios details, analagous to the printout from
    print_scenarios().

    Args:
        scenarios (list[dict[str, Any]]): Scenario groups to show.
        runs_df (pandas.DataFrame): Benchmark runs to retrieve the scenario
            data from.
        min_count (int): Only show scenarios with at least this many rows.

    Returns:
        pandas.DataFrame: Details about available scenarios
    """

    # Make a column name utilizing bound prefixes
    def col_name(col: str) -> str:
        if col[:BOUND_PREFIX_LEN] in COLUMN_BOUND_STR:
            return col[BOUND_PREFIX_LEN:] + \
                COLUMN_BOUND_STR[col[:BOUND_PREFIX_LEN]]
        return col

    # Make DataFrame with matching row counts, and columns values from scenario
    schema = {
        'Count': pd.Series(dtype='int'),
    }

    if scenarios:
        # If scenarios is empty, we will end up with a DataFrame having only
        # a 'Count' column and no rows
        for col in scenarios[0].keys():
            schema[col_name(col)] = pd.Series(
                dtype=COLUMNS[col_base(col)].dtype)
    df = pd.DataFrame(schema)

    # Populate DataFrame
    counts = get_scenario_counts(runs_df, scenarios)
    for ii, sc in enumerate(scenarios):
        if counts[ii] < min_count:
            continue
        row = {'Count': counts[ii]}
        for col, val in sc.items():
            row[col_name(col)] = val
        # Index of DataFrame will have 1:1 correspondance with scenario index
        df.loc[ii] = row

    return df


def get_meet_slo_df(
        runs_df: pd.DataFrame,
        slos: list[SLO]) -> pd.DataFrame:
    """Get rows from dataset meeting provided SLOs.

    Args:
        runs_df (pandas.DataFrame): Dataset to search.
        slos (list[SLO]): SLOs to meet.

    Returns:
        pandas.DataFrame: Rows matching SLOs
    """
    runs_meet_slo_df = runs_df
    for slo in slos:
        if COLUMNS[slo.col].pref == Pref.LOW:
            # Must be less than or equal to SLO value to meet SLO
            runs_meet_slo_df = runs_meet_slo_df[runs_meet_slo_df[slo.col].__le__(
                slo.value)]
        elif COLUMNS[slo.col].pref == Pref.HIGH:
            # Must be greater than or equal to SLO value to meet SLO
            runs_meet_slo_df = runs_meet_slo_df[runs_meet_slo_df[slo.col].__ge__(
                slo.value)]
        else:
            raise Exception(f'Invalid SLO: {slo.col}')
    return runs_meet_slo_df


def get_pareto_front_df(
        runs_df: pd.DataFrame,
        col_a: str,
        col_b: str,
        sort: bool = False) -> pd.DataFrame:
    """Get rows from dataset on Pareto front for the provided metrics.

    Args:
        runs_df (pandas.DataFrame): Dataset to search.
        col_a (str): First metric column to optimize.
        col_b (str): Second metric column to optimize.
        sort (bool): Sort results

    Returns:
        pandas.DataFrame: Rows on the Pareto front.
    """
    # Make sure columns have a preferred direction
    if COLUMNS[col_a].pref == Pref.NEUTRAL:
        raise Exception(f'Column does not have a preferred direction: {col_a}')
    if COLUMNS[col_b].pref == Pref.NEUTRAL:
        raise Exception(f'Column does not have a preferred direction: {col_b}')

    def better(a: Any, b: Any, col: str) -> bool:
        """Return true if column in 'a' is better than 'b'."""
        if COLUMNS[col].pref == Pref.LOW:
            return a[col] < b[col]
        if COLUMNS[col].pref == Pref.HIGH:
            return a[col] > b[col]
        raise Exception(f'Invalid preference for column: {col}')

    pareto_set = set(runs_df.index.tolist())
    for ii, rowa in runs_df.iterrows():
        is_pareto_front = runs_df.index.isin(pareto_set)
        for jj, rowb in runs_df[is_pareto_front].iterrows():
            if ii == jj:
                continue
            if better(rowa, rowb, col_a) and better(rowa, rowb, col_b):
                # Index jj worse in all ways to index ii
                pareto_set.remove(jj)
    if sort:
        return runs_df[runs_df.index.isin(pareto_set)].sort_values(by=col_a)
    else:
        # Preserve order
        return runs_df[runs_df.index.isin(pareto_set)]
