#!/usr/bin/env python3

# This script imports data from a benchmark run in llm-d-benchmark using any
# supported harness, and converts the results into a data file with a standard
# benchmark report format. This format can then be used for post processing
# that is not specialized to a particular harness.

import argparse
import datetime
import os
import re
import sys
import yaml

import numpy as np
from scipy import stats

from schema import BenchmarkReport, Units, WorkloadGenerator


def check_file(file_path: str) -> None:
    """Make sure regular file exists.

    Args:
        file_path (str): File to check.
    """
    if not os.path.exists(file_path):
        sys.stderr.write('File does not exist: %s\n' % file_path)
        exit(2)
    if not os.path.isfile(file_path):
        sys.stderr.write('Not a regular file: %s\n' % file_path)
        exit(2)


def import_yaml(file_path: str) -> dict[any, any]:
    """Import a JSON/YAML file as a dict.

    Args:
        file_path (str): Path to JSON/YAML file.

    Returns:
        dict: Imported data.
    """
    check_file(file_path)
    with open(file_path, 'r', encoding='UTF-8') as file:
        data = yaml.safe_load(file)
    return data


def import_csv_with_header(file_path: str) -> dict[str, list[any]]:
    """Import a CSV file where the first line is a header.

    Args:
        file_path (str): Path to CSV file.

    Returns:
        dict: Imported data where the header provides key names.
    """
    check_file(file_path)
    with open(file_path, 'r', encoding='UTF-8') as file:
        for ii, line in enumerate(file):
            if ii == 0:
                headers: list[str] = list(map(str.strip, line.split(',')))
                data: dict[str, list[any]] = {}
                for hdr in headers:
                    data[hdr] = []
                continue
            row_vals = list(map(str.strip, line.split(',')))
            if len(row_vals) != len(headers):
                sys.stderr.write('Warning: line %d of "%s" does not match header length, skipping: %ds != %d\n' %
                ii + 1, file_path, len(row_vals), len(headers))
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


def import_variables(file_path: str) -> dict[str, str]:
    """Import a list of environment variable definitions from file as a dict.

    Args:
        file_path (str): Path to variable definition file.

    Returns:
        dict: Imported data.
    """
    check_file(file_path)

    envars = {}
    with open(file_path, 'r', encoding='UTF-8') as file:
        for line in file:
            if not '=' in line:
                continue
            envar, value = line.strip().split('=', 1)
            if re.search('^export ', envar):
                envar = envar[7:].strip()
            envars[envar] = value

    return envars


def update_dict(dest: dict[any, any], source: dict[any, any]) -> None:
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


#TODO if a variables file exists, it is assumed it contains certain variable
# definitions. If any are missing, this function will crash.
def _import_llmd_benchmark_run_data(results_path: str) -> dict:
    """Import scenario data from llm-d-benchmark run given the results path.

    This step is required because llm-d-benchmark standup details are not
    passed along to the harness pods. When a harness pod creates a benchmark
    report, it will fill in only the details it knows.

    Args:
        results_path (str): Path to results directory.

    Returns:
        dict: Imported data about scenario following schema of BenchmarkReport.
    """
    variables_file = os.path.join(results_path, os.pardir, os.pardir, 'environment', 'variables')
    if not os.path.isfile(variables_file):
        # We do not have this information available to us.
        return {}

    envars = import_variables(variables_file)

    if envars['LLMDBENCH_DEPLOY_METHODS'] == 'standalone':
        config = {
            "scenario": {
                "model": {
                    "name": envars['LLMDBENCH_DEPLOY_MODEL_LIST'] # TODO this will only work if not a list of models
                },
                "host": {
                    "type": ['replica'] * int(envars['LLMDBENCH_VLLM_COMMON_REPLICAS']),
                    "accelerator": [{
                        "model": envars['LLMDBENCH_VLLM_COMMON_AFFINITY'].split(':', 1)[-1],
                        "count": int(envars['LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR']),
                        "parallelism": {
                            "tp": int(envars['LLMDBENCH_VLLM_COMMON_ACCELERATOR_NR']),
                        },
                    }] * int(envars['LLMDBENCH_VLLM_COMMON_REPLICAS']),
                },
                "platform": {
                    "engine": [{
                        "name": envars['LLMDBENCH_VLLM_STANDALONE_IMAGE_REGISTRY'] + \
                                envars['LLMDBENCH_VLLM_STANDALONE_IMAGE_REPO'] + \
                                envars['LLMDBENCH_VLLM_STANDALONE_IMAGE_NAME'] + \
                                envars['LLMDBENCH_VLLM_STANDALONE_IMAGE_TAG'],
                    }] * int(envars['LLMDBENCH_VLLM_COMMON_REPLICAS'])
                },
            },
        }
    else:
        config = {
            "scenario": {
                "model": {
                    "name": envars['LLMDBENCH_DEPLOY_MODEL_LIST'] # TODO this will only work if not a list of models
                },
                "host": {
                    "type": ['prefill'] * int(envars['LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS']) + \
                            ['decode'] * int(envars['LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS']),
                    "accelerator": [{
                        "model": envars['LLMDBENCH_VLLM_COMMON_AFFINITY'].split(':', 1)[-1],
                        "count": int(envars['LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR']),
                        "parallelism": {
                            "tp": int(envars['LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR']),
                        },
                    }] * int(envars['LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS']) + \
                    [{
                        "model": envars['LLMDBENCH_VLLM_COMMON_AFFINITY'].split(':', 1)[-1],
                        "count": int(envars['LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR']),
                        "parallelism": {
                            "tp": int(envars['LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR']),
                        },
                    }] * int(envars['LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS']),
                },
                "platform": {
                    "engine": [{
                            "name": envars['LLMDBENCH_LLMD_IMAGE_REGISTRY'] + \
                                    envars['LLMDBENCH_LLMD_IMAGE_REPO'] + \
                                    envars['LLMDBENCH_LLMD_IMAGE_NAME'] + \
                                    envars['LLMDBENCH_LLMD_IMAGE_TAG'],
                    }] * (int(envars['LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS']) +
                         int(envars['LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS']))
                },
            },
        }

    return config


def import_benchmark_report(br_file: str) -> BenchmarkReport:
    """Import benchmark report, and supplement with additional data from llm-d-benchmark run.

    Args:
        br_file (str): Benchmark report file to import.

    Returns:
        BenchmarkReport: Imported benchmark report supplemented with run data.
    """
    check_file(br_file)

    # Import benchmark report as a dict following the schema of BenchmarkReport
    br_dict = import_yaml(br_file)

    # Append to that dict the data from llm-d-benchmark standup.
    # We must append the data from the llm-d-benchmark to the data from the
    # harness, rather than the reverse, as the fmperf harness does not record
    # the model name (it will be filled in with "unknown" during benchmark
    # report generation).
    update_dict(br_dict, _import_llmd_benchmark_run_data(os.path.dirname(br_file)))

    return BenchmarkReport(**br_dict)


def _vllm_timestamp_to_epoch(date_str: str) -> int:
    """Convert timestamp from vLLM benchmark into seconds from Unix epoch.

    String format is YYYYMMDD-HHMMSS in UTC.

    Args:
        date_str (str): Timestamp from vLLM benchmark.

    Returns:
        int: Seconds from Unix epoch.
    """
    date_str = date_str.strip()
    if not re.search('[0-9]{8}-[0-9]{6}', date_str):
        raise Exception('Invalid date format: %s' % date_str)
    year = int(date_str[0:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])
    hour = int(date_str[9:11])
    minute = int(date_str[11:13])
    second = int(date_str[13:15])
    return datetime.datetime(year, month, day, hour, minute, second).timestamp()


def import_vllm_benchmark(results_file: str) -> BenchmarkReport:
    """Import data from a vLLM benchmark run as a BenchmarkReport.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReport: Imported data.
    """
    check_file(results_file)

    # Import results file from vLLM benchmark
    results = import_yaml(results_file)

    # Import scenario details from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReport
    br_dict = _import_llmd_benchmark_run_data(os.path.dirname(results_file))
    # Append to that dict the data from vLLM benchmark
    update_dict(br_dict, {
        "scenario": {
            "model": {"name": results['model_id']},
            "load": {
                "name": WorkloadGenerator.VLLM_BENCHMARK,
                "args": {
                    "num_prompts": results['num_prompts'],
                    "request_rate": results['request_rate'],
                    "burstiness":results['burstiness'],
                    "max_concurrency": results['max_concurrency'],
                },
            },
        },
        "metrics": {
            "time": {
                "duration": results['duration'],
                "start": _vllm_timestamp_to_epoch(results['date']),
            },
            "requests": {
                "total": results['completed'],
                "input_length": {
                    "units": Units.COUNT,
                    "mean": results['total_input_tokens']/results['completed'],
                },
                "output_length": {
                    "units": Units.COUNT,
                    "mean": results['total_output_tokens']/results['completed'],
                },
            },
            "latency": {
                "time_to_first_token": {
                    "units": Units.MS,
                    "mean": results['mean_ttft_ms'],
                    "median": results['median_ttft_ms'],
                    "stddev": results['std_ttft_ms'],
                    "p90": results['p90_ttft_ms'],
                    "p95": results['p95_ttft_ms'],
                    "p99": results['p99_ttft_ms'],
                },
                "time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['mean_tpot_ms'],
                    "median": results['median_tpot_ms'],
                    "stddev": results['std_tpot_ms'],
                    "p90": results['p90_tpot_ms'],
                    "p95": results['p95_tpot_ms'],
                    "p99": results['p99_tpot_ms'],
                },
                "inter_token_latency": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['mean_itl_ms'],
                    "median": results['median_itl_ms'],
                    "stddev": results['std_itl_ms'],
                    "p90": results['p90_itl_ms'],
                    "p95": results['p95_itl_ms'],
                    "p99": results['p99_itl_ms'],
                },
                "request_latency": {
                    "units": Units.MS,
                    "mean": results['mean_e2el_ms'],
                    "median": results['median_e2el_ms'],
                    "stddev": results['std_e2el_ms'],
                    "p90": results['p90_e2el_ms'],
                    "p95": results['p95_e2el_ms'],
                    "p99": results['p99_e2el_ms'],
                },
            },
            "throughput": {
                "output_tokens_per_sec": results['output_throughput'],
                "total_tokens_per_sec": results['total_token_throughput'],
                "requests_per_sec": results['request_throughput'],
            },
        },
    })

    return BenchmarkReport(**br_dict)


def import_guidellm(results_file: str) -> BenchmarkReport:
    """Import data from a GuideLLM run as a BenchmarkReport.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReport: Imported data.
    """
    check_file(results_file)

    # Everything falls under ['benchmarks'][0], so just grab that part
    results = import_yaml(results_file)['benchmarks'][0]

    # Import scenario details from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReport
    br_dict = _import_llmd_benchmark_run_data(os.path.dirname(results_file))
    # Append to that dict the data from GuideLLM
    update_dict(br_dict, {
        "scenario": {
            "model": {"name": results['worker']['backend_model']},
            "load": {
                "name": WorkloadGenerator.GUIDELLM,
                "args": results['args'],
            },
        },
        "metrics": {
            "time": {
                "duration": results['duration'],
                "start": results['start_time'],
                "stop": results['end_time'],
            },
            "requests": {
                "total": results['request_totals']['total'],
                "failures": results['request_totals']['errored'],
                "incomplete": results['request_totals']['incomplete'],
                "input_length": {
                    "units": Units.COUNT,
                    "mean": results['metrics']['prompt_token_count']['successful']['mean'],
                    "median": results['metrics']['prompt_token_count']['successful']['median'],
                    "mode": results['metrics']['prompt_token_count']['successful']['mode'],
                    "stddev": results['metrics']['prompt_token_count']['successful']['std_dev'],
                    "min": results['metrics']['prompt_token_count']['successful']['min'],
                    "p001": results['metrics']['prompt_token_count']['successful']['percentiles']['p001'],
                    "p01": results['metrics']['prompt_token_count']['successful']['percentiles']['p01'],
                    "p05": results['metrics']['prompt_token_count']['successful']['percentiles']['p05'],
                    "p10": results['metrics']['prompt_token_count']['successful']['percentiles']['p10'],
                    "p25": results['metrics']['prompt_token_count']['successful']['percentiles']['p25'],
                    "p50": results['metrics']['prompt_token_count']['successful']['percentiles']['p50'],
                    "p75": results['metrics']['prompt_token_count']['successful']['percentiles']['p75'],
                    "p90": results['metrics']['prompt_token_count']['successful']['percentiles']['p90'],
                    "p95": results['metrics']['prompt_token_count']['successful']['percentiles']['p95'],
                    "p99": results['metrics']['prompt_token_count']['successful']['percentiles']['p99'],
                    "p999": results['metrics']['prompt_token_count']['successful']['percentiles']['p999'],
                    "max": results['metrics']['prompt_token_count']['successful']['max'],
                },
                "output_length": {
                    "units": Units.COUNT,
                    "mean": results['metrics']['output_token_count']['successful']['mean'],
                    "median": results['metrics']['output_token_count']['successful']['median'],
                    "mode": results['metrics']['output_token_count']['successful']['mode'],
                    "stddev": results['metrics']['output_token_count']['successful']['std_dev'],
                    "min": results['metrics']['output_token_count']['successful']['min'],
                    "p001": results['metrics']['output_token_count']['successful']['percentiles']['p001'],
                    "p01": results['metrics']['output_token_count']['successful']['percentiles']['p01'],
                    "p05": results['metrics']['output_token_count']['successful']['percentiles']['p05'],
                    "p10": results['metrics']['output_token_count']['successful']['percentiles']['p10'],
                    "p25": results['metrics']['output_token_count']['successful']['percentiles']['p25'],
                    "p50": results['metrics']['output_token_count']['successful']['percentiles']['p50'],
                    "p75": results['metrics']['output_token_count']['successful']['percentiles']['p75'],
                    "p90": results['metrics']['output_token_count']['successful']['percentiles']['p90'],
                    "p95": results['metrics']['output_token_count']['successful']['percentiles']['p95'],
                    "p99": results['metrics']['output_token_count']['successful']['percentiles']['p99'],
                    "p999": results['metrics']['output_token_count']['successful']['percentiles']['p999'],
                    "max": results['metrics']['output_token_count']['successful']['max'],
                },
            },
            "latency": {
                "time_to_first_token": {
                    "units": Units.MS,
                    "mean": results['metrics']['time_to_first_token_ms']['successful']['mean'],
                    "median": results['metrics']['time_to_first_token_ms']['successful']['median'],
                    "mode": results['metrics']['time_to_first_token_ms']['successful']['mode'],
                    "stddev": results['metrics']['time_to_first_token_ms']['successful']['std_dev'],
                    "min": results['metrics']['time_to_first_token_ms']['successful']['min'],
                    "p001": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p001'],
                    "p01": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p01'],
                    "p05": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p05'],
                    "p10": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p10'],
                    "p25": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p25'],
                    "p50": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p50'],
                    "p75": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p75'],
                    "p90": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p90'],
                    "p95": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p95'],
                    "p99": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p99'],
                    "p999": results['metrics']['time_to_first_token_ms']['successful']['percentiles']['p999'],
                    "max": results['metrics']['time_to_first_token_ms']['successful']['max'],
                },
                "time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['metrics']['time_per_output_token_ms']['successful']['mean'],
                    "median": results['metrics']['time_per_output_token_ms']['successful']['median'],
                    "mode": results['metrics']['time_per_output_token_ms']['successful']['mode'],
                    "stddev": results['metrics']['time_per_output_token_ms']['successful']['std_dev'],
                    "min": results['metrics']['time_per_output_token_ms']['successful']['min'],
                    "p001": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p001'],
                    "p01": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p01'],
                    "p05": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p05'],
                    "p10": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p10'],
                    "p25": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p25'],
                    "p50": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p50'],
                    "p75": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p75'],
                    "p90": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p90'],
                    "p95": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p95'],
                    "p99": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p99'],
                    "p999": results['metrics']['time_per_output_token_ms']['successful']['percentiles']['p999'],
                    "max": results['metrics']['time_per_output_token_ms']['successful']['max'],
                },
                "inter_token_latency": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['metrics']['inter_token_latency_ms']['successful']['mean'],
                    "median": results['metrics']['inter_token_latency_ms']['successful']['median'],
                    "mode": results['metrics']['inter_token_latency_ms']['successful']['mode'],
                    "stddev": results['metrics']['inter_token_latency_ms']['successful']['std_dev'],
                    "min": results['metrics']['inter_token_latency_ms']['successful']['min'],
                    "p001": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p001'],
                    "p01": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p01'],
                    "p05": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p05'],
                    "p10": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p10'],
                    "p25": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p25'],
                    "p50": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p50'],
                    "p75": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p75'],
                    "p90": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p90'],
                    "p95": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p95'],
                    "p99": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p99'],
                    "p999": results['metrics']['inter_token_latency_ms']['successful']['percentiles']['p999'],
                    "max": results['metrics']['inter_token_latency_ms']['successful']['max'],
                },
                "request_latency": {
                    "units": Units.MS,
                    "mean": results['metrics']['request_latency']['successful']['mean'],
                    "median": results['metrics']['request_latency']['successful']['median'],
                    "mode": results['metrics']['request_latency']['successful']['mode'],
                    "stddev": results['metrics']['request_latency']['successful']['std_dev'],
                    "min": results['metrics']['request_latency']['successful']['min'],
                    "p001": results['metrics']['request_latency']['successful']['percentiles']['p001'],
                    "p01": results['metrics']['request_latency']['successful']['percentiles']['p01'],
                    "p05": results['metrics']['request_latency']['successful']['percentiles']['p05'],
                    "p10": results['metrics']['request_latency']['successful']['percentiles']['p10'],
                    "p25": results['metrics']['request_latency']['successful']['percentiles']['p25'],
                    "p50": results['metrics']['request_latency']['successful']['percentiles']['p50'],
                    "p75": results['metrics']['request_latency']['successful']['percentiles']['p75'],
                    "p90": results['metrics']['request_latency']['successful']['percentiles']['p90'],
                    "p95": results['metrics']['request_latency']['successful']['percentiles']['p95'],
                    "p99": results['metrics']['request_latency']['successful']['percentiles']['p99'],
                    "p999": results['metrics']['request_latency']['successful']['percentiles']['p999'],
                    "max": results['metrics']['request_latency']['successful']['max'],
                },
            },
            "throughput": {
                "output_tokens_per_sec": results['metrics']['output_tokens_per_second']['successful']['mean'],
                "total_tokens_per_sec": results['metrics']['tokens_per_second']['successful']['mean'],
                "requests_per_sec": results['metrics']['requests_per_second']['successful']['mean'],
            },
        },
    })

    return BenchmarkReport(**br_dict)


def import_fmperf(results_file: str) -> BenchmarkReport:
    """Import data from a fmperf run as a BenchmarkReport.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReport: Imported data.
    """
    check_file(results_file)

    results = import_csv_with_header(results_file)

    # Import scenario details from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReport
    br_dict = _import_llmd_benchmark_run_data(os.path.dirname(results_file))
    if br_dict:
        model_name = br_dict['scenario']['model']['name']
    else:
        model_name = "unknown"
    # Append to that dict the data from fmperf
    duration = results['finish_time'][-1] - results['launch_time'][0]
    req_latency = results['finish_time'] - results['launch_time']
    tpot = (req_latency - results['ttft']) / (results['generation_tokens'] - 1)
    itl = tpot
    update_dict(br_dict, {
        "scenario": {
            "model": {"name": model_name},
            "load": {
                "name": WorkloadGenerator.FMPERF,
            },
        },
        "metrics": {
            "time": {
                "duration": duration,
                "start": results['launch_time'][0],
                "stop": results['finish_time'][-1],
            },
            "requests": {
                "total": len(results['prompt_tokens']),
                "input_length": {
                    "units": Units.COUNT,
                    "mean": results['prompt_tokens'].mean(),
                    "median": np.median(results['prompt_tokens']),
                    "mode": stats.mode(results['prompt_tokens'])[0],
                    "stddev": results['prompt_tokens'].std(),
                    "min": results['prompt_tokens'].min(),
                    "p001": np.percentile(results['prompt_tokens'], 0.1),
                    "p01": np.percentile(results['prompt_tokens'], 1),
                    "p05": np.percentile(results['prompt_tokens'], 5),
                    "p10": np.percentile(results['prompt_tokens'], 10),
                    "p25": np.percentile(results['prompt_tokens'], 25),
                    "p50": np.percentile(results['prompt_tokens'], 50),
                    "p75": np.percentile(results['prompt_tokens'], 75),
                    "p90": np.percentile(results['prompt_tokens'], 90),
                    "p95": np.percentile(results['prompt_tokens'], 95),
                    "p99": np.percentile(results['prompt_tokens'], 99),
                    "p999": np.percentile(results['prompt_tokens'], 99.9),
                    "max": results['prompt_tokens'].max(),
                },
                "output_length": {
                    "units": Units.COUNT,
                    "mean": results['generation_tokens'].mean(),
                    "median": np.median(results['generation_tokens']),
                    "mode": stats.mode(results['generation_tokens'])[0],
                    "stddev": results['generation_tokens'].std(),
                    "min": results['generation_tokens'].min(),
                    "p001": np.percentile(results['generation_tokens'], 0.1),
                    "p01": np.percentile(results['generation_tokens'], 1),
                    "p05": np.percentile(results['generation_tokens'], 5),
                    "p10": np.percentile(results['generation_tokens'], 10),
                    "p25": np.percentile(results['generation_tokens'], 25),
                    "p50": np.percentile(results['generation_tokens'], 50),
                    "p75": np.percentile(results['generation_tokens'], 75),
                    "p90": np.percentile(results['generation_tokens'], 90),
                    "p95": np.percentile(results['generation_tokens'], 95),
                    "p99": np.percentile(results['generation_tokens'], 99),
                    "p999": np.percentile(results['generation_tokens'], 99.9),
                    "max": results['generation_tokens'].max(),
                },
            },
            "latency": {
                "time_to_first_token": {
                    "units": Units.MS,
                    "mean": results['ttft'].mean(),
                    "median": np.median(results['ttft']),
                    "mode": stats.mode(results['ttft'])[0],
                    "stddev": results['ttft'].std(),
                    "min": results['ttft'].min(),
                    "p001": np.percentile(results['ttft'], 0.1),
                    "p01": np.percentile(results['ttft'], 1),
                    "p05": np.percentile(results['ttft'], 5),
                    "p10": np.percentile(results['ttft'], 10),
                    "p25": np.percentile(results['ttft'], 25),
                    "p50": np.percentile(results['ttft'], 50),
                    "p75": np.percentile(results['ttft'], 75),
                    "p90": np.percentile(results['ttft'], 90),
                    "p95": np.percentile(results['ttft'], 95),
                    "p99": np.percentile(results['ttft'], 99),
                    "p999": np.percentile(results['ttft'], 99.9),
                    "max": results['ttft'].max(),
                },
                "time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": tpot.mean(),
                    "median": np.median(tpot),
                    "mode": stats.mode(tpot)[0],
                    "stddev": tpot.std(),
                    "min": tpot.min(),
                    "p001": np.percentile(tpot, 0.1),
                    "p01": np.percentile(tpot, 1),
                    "p05": np.percentile(tpot, 5),
                    "p10": np.percentile(tpot, 10),
                    "p25": np.percentile(tpot, 25),
                    "p50": np.percentile(tpot, 50),
                    "p75": np.percentile(tpot, 75),
                    "p90": np.percentile(tpot, 90),
                    "p95": np.percentile(tpot, 95),
                    "p99": np.percentile(tpot, 99),
                    "p999": np.percentile(tpot, 99.9),
                    "max": tpot.max(),
                },
                "inter_token_latency": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": itl.mean(),
                    "median": np.median(itl),
                    "mode": stats.mode(itl)[0],
                    "stddev": itl.std(),
                    "min": itl.min(),
                    "p001": np.percentile(itl, 0.1),
                    "p01": np.percentile(itl, 1),
                    "p05": np.percentile(itl, 5),
                    "p10": np.percentile(itl, 10),
                    "p25": np.percentile(itl, 25),
                    "p50": np.percentile(itl, 50),
                    "p75": np.percentile(itl, 75),
                    "p90": np.percentile(itl, 90),
                    "p95": np.percentile(itl, 95),
                    "p99": np.percentile(itl, 99),
                    "p999": np.percentile(itl, 99.9),
                    "max": itl.max(),
                },
                "request_latency": {
                    "units": Units.MS,
                    "mean": req_latency.mean(),
                    "median": np.median(req_latency),
                    "mode": stats.mode(req_latency)[0],
                    "stddev": req_latency.std(),
                    "min": req_latency.min(),
                    "p001": np.percentile(req_latency, 0.1),
                    "p01": np.percentile(req_latency, 1),
                    "p05": np.percentile(req_latency, 5),
                    "p10": np.percentile(req_latency, 10),
                    "p25": np.percentile(req_latency, 25),
                    "p50": np.percentile(req_latency, 50),
                    "p75": np.percentile(req_latency, 75),
                    "p90": np.percentile(req_latency, 90),
                    "p95": np.percentile(req_latency, 95),
                    "p99": np.percentile(req_latency, 99),
                    "p999": np.percentile(req_latency, 99.9),
                    "max": req_latency.max(),
                },
            },
            "throughput": {
                "output_tokens_per_sec": results['generation_tokens'].sum()/duration,
                "total_tokens_per_sec": (results['prompt_tokens'].sum() + results['generation_tokens'].sum())/duration,
                "requests_per_sec": len(results['prompt_tokens'])/duration,
            },
        },
    })

    return BenchmarkReport(**br_dict)


def import_inference_perf(results_file: str) -> BenchmarkReport:
    """Import data from a Inference Perf run as a BenchmarkReport.

    Args:
        results_file (str): Results file to import.

    Returns:
        BenchmarkReport: Imported data.
    """
    check_file(results_file)

    # Import results from Inference Perf
    results = import_yaml(results_file)

    # Import the "per_request_lifecycle_metrics.json" from Inference Perf, as
    # it contains additional information we need (the model name)
    per_req_file = os.path.join(
        os.path.dirname(results_file),
        'per_request_lifecycle_metrics.json'
    )
    per_req = import_yaml(per_req_file)

    # Import scenario details from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReport
    br_dict = _import_llmd_benchmark_run_data(os.path.dirname(results_file))
    # Append to that dict the data from Inference Perf
    update_dict(br_dict, {
        "scenario": {
            "model": {"name": yaml.safe_load(per_req[0]['request'])['model']},
            "load": {
                "name": WorkloadGenerator.INFERENCE_PERF,
            },
        },
        "metrics": {
            "time": {
                "duration": results['load_summary']['send_duration'], # TODO this isn't exactly what we need, we may need to pull apart per_request_lifecycle_metrics.json
            },
            "requests": {
                "total": results['load_summary']['count'],
                "failures": results['failures']['count'],
                "input_length": {
                    "units": Units.COUNT,
                    "mean": results['successes']['prompt_len']['mean'],
                    "min": results['successes']['prompt_len']['min'],
                    "p10": results['successes']['prompt_len']['p10'],
                    "p50": results['successes']['prompt_len']['p50'],
                    "p90": results['successes']['prompt_len']['p90'],
                    "max": results['successes']['prompt_len']['max'],
                },
                "output_length": {
                    "units": Units.COUNT,
                    "mean": results['successes']['output_len']['mean'],
                    "min": results['successes']['output_len']['min'],
                    "p10": results['successes']['output_len']['p10'],
                    "p50": results['successes']['output_len']['p50'],
                    "p90": results['successes']['output_len']['p90'],
                    "max": results['successes']['output_len']['max'],
                },
            },
            "latency": {
                "time_to_first_token": {
                    "units": Units.MS,
                    "mean": results['successes']['latency']['time_to_first_token']['mean'],
                    "min": results['successes']['latency']['time_to_first_token']['min'],
                    "p10": results['successes']['latency']['time_to_first_token']['p10'],
                    "p50": results['successes']['latency']['time_to_first_token']['p50'],
                    "p90": results['successes']['latency']['time_to_first_token']['p90'],
                    "max": results['successes']['latency']['time_to_first_token']['max'],
                },
                "normalized_time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['successes']['latency']['normalized_time_per_output_token']['mean'],
                    "min": results['successes']['latency']['normalized_time_per_output_token']['min'],
                    "p10": results['successes']['latency']['normalized_time_per_output_token']['p10'],
                    "p50": results['successes']['latency']['normalized_time_per_output_token']['p50'],
                    "p90": results['successes']['latency']['normalized_time_per_output_token']['p90'],
                    "max": results['successes']['latency']['normalized_time_per_output_token']['max'],
                },
                "time_per_output_token": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['successes']['latency']['time_per_output_token']['mean'],
                    "min": results['successes']['latency']['time_per_output_token']['min'],
                    "p10": results['successes']['latency']['time_per_output_token']['p10'],
                    "p50": results['successes']['latency']['time_per_output_token']['p50'],
                    "p90": results['successes']['latency']['time_per_output_token']['p90'],
                    "max": results['successes']['latency']['time_per_output_token']['max'],
                },
                "inter_token_latency": {
                    "units": Units.MS_PER_TOKEN,
                    "mean": results['successes']['latency']['inter_token_latency']['mean'],
                    "min": results['successes']['latency']['inter_token_latency']['min'],
                    "p10": results['successes']['latency']['inter_token_latency']['p10'],
                    "p50": results['successes']['latency']['inter_token_latency']['p50'],
                    "p90": results['successes']['latency']['inter_token_latency']['p90'],
                    "max": results['successes']['latency']['inter_token_latency']['max'],
                },
                "request_latency": {
                    "units": Units.MS,
                    "mean": results['successes']['latency']['request_latency']['mean'],
                    "min": results['successes']['latency']['request_latency']['min'],
                    "p10": results['successes']['latency']['request_latency']['p10'],
                    "p50": results['successes']['latency']['request_latency']['p50'],
                    "p90": results['successes']['latency']['request_latency']['p90'],
                    "max": results['successes']['latency']['request_latency']['max'],
                },
            },
            "throughput": {
                "output_tokens_per_sec": results['successes']['throughput']['output_tokens_per_sec'],
                "total_tokens_per_sec": results['successes']['throughput']['total_tokens_per_sec'],
                "requests_per_sec": results['successes']['throughput']['requests_per_sec'],
            },
        },
    })

    return BenchmarkReport(**br_dict)


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='Convert benchmark run data to standard benchmark report format.')
    parser.add_argument(
        'results_file',
        type=str,
        help='Results file to convert.')
    parser.add_argument(
        'output_file',
        type=str,
        default=None,
        nargs='?',
        help='Output file for benchark report.')
    parser.add_argument(
        '-f', '--force',
        action=argparse.BooleanOptionalAction,
        help='Write to output file even if it already exists.')
    parser.add_argument(
        '-w', '--workload-generator',
        type=str,
        default=WorkloadGenerator.VLLM_BENCHMARK,
        help='Workload generator used.')

    args = parser.parse_args()
    if args.output_file and os.path.exists(args.output_file) and not args.force:
        sys.stderr.write('Output file already exists: %s\n' % args.output_file)
        sys.exit(1)

    match args.workload_generator:
        case WorkloadGenerator.FMPERF:
            if args.output_file:
                import_fmperf(args.results_file).export_yaml(args.output_file)
            else:
                import_fmperf(args.results_file).print_yaml()
        case WorkloadGenerator.GUIDELLM:
            if args.output_file:
                import_guidellm(args.results_file).export_yaml(args.output_file)
            else:
                import_guidellm(args.results_file).print_yaml()
        case WorkloadGenerator.INFERENCE_PERF:
            if args.output_file:
                import_inference_perf(args.results_file).export_yaml(args.output_file)
            else:
                import_inference_perf(args.results_file).print_yaml()
        case WorkloadGenerator.VLLM_BENCHMARK:
            if args.output_file:
                import_vllm_benchmark(args.results_file).export_yaml(args.output_file)
            else:
                import_vllm_benchmark(args.results_file).print_yaml()
        case _:
            sys.stderr.write('Unsupported workload generator: %s\n' %
                args.workload_generator)
            sys.stderr.write('Must be one of: %s\n' %
                str([wg.value for wg in WorkloadGenerator])[1:-1])
            sys.exit(1)
