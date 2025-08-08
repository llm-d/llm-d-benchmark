#!/usr/bin/env python3

# This script imports data from a benchmark run in llm-d-benchmark using any
# supported harness, and converts the results into a data file with a
# standardized format. This format can then be used for post processing that is
# not specialized to a particular harness.

import argparse
import datetime
import os
import re
import sys
import yaml

from schema import BenchmarkReport, Units, WorkloadGenerator


def import_yaml(file_path: str) -> dict[any, any]:
    """Import a JSON/YAML file as a dict.

    Args:
        file_path (str): Path to JSON/YAML file.

    Returns:
        dict: Imported data.
    """
    if not os.path.isfile(file_path):
        raise Exception('File does not exist: %s' % file_path)
    with open(file_path, 'r', encoding='UTF-8') as file:
        data = yaml.safe_load(file)
    return data


def import_variables(file_path: str) -> dict[str, str]:
    """Import a list of environment variable definitions from file as a dict.

    Args:
        file_path (str): Path to variable definition file.

    Returns:
        dict: Imported data.
    """
    if not os.path.isfile(file_path):
        raise Exception('File does not exist: %s' % file_path)

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


def import_vllm_benchmark(results_path: str) -> BenchmarkReport:
    """Import data from a vLLM benchmark run as a BenchmarkReport.

    Args:
        results_path (str): Path to results directory.

    Returns:
        BenchmarkReport: Imported data.
    """
    if not os.path.isdir(results_path):
        raise Exception('Invalid results path: %s' % results_path)

    results_files = []
    # Sort data files, assuming this correlates with age. This assumption is
    # only true if the filename includes the date and no other features of the
    # filename are modified.
    for file in sorted(os.listdir(results_path)):
        if not re.search('^vllm.+\\.json$', file):
            # Skip files that do not match result data filename
            continue
        results_files.append(file)

    if len(results_files) == 0:
        raise Exception('No results file exists: %s' % results_path)
    if len(results_files) > 1:
        sys.stderr.write('Warning: multiple results files exist, selecting last: %s\n' %
            results_files)

    # Import results file from vLLM benchmark
    results = import_yaml(os.path.join(results_path, results_files[-1]))

    # Import scenario details from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReport
    br_dict = _import_llmd_benchmark_run_data(results_path)
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


def import_guidellm(results_path: str) -> BenchmarkReport:
    """Import data from a GuideLLM run as a BenchmarkReport.

    Args:
        results_path (str): Path to results directory.

    Returns:
        BenchmarkReport: Imported data.
    """
    if not os.path.isdir(results_path):
        raise Exception('Invalid results path: %s' % results_path)
    # The GuideLLM harness for llm-d-benchmark saves results to results.json
    if not os.path.isfile(os.path.join(results_path, 'results.json')):
        raise Exception('Missing "results.json": %s' % results_path)

    # Everything falls under ['benchmarks'][0], so just grab that part
    results = import_yaml(os.path.join(results_path, 'results.json'))['benchmarks'][0]

    # Import scenario details from llm-d-benchmark run as a dict following the
    # schema of BenchmarkReport
    br_dict = _import_llmd_benchmark_run_data(results_path)
    # Append to that dict the data from vLLM benchmark
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


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description='Convert benchmark run data to standard format.')
    parser.add_argument(
        'results_path',
        type=str,
        help='Path to results directory.')
    parser.add_argument(
        '-w', '--workload-generator',
        type=str,
        default=WorkloadGenerator.VLLM_BENCHMARK,
        help='Workload generator used.')

    args = parser.parse_args()

    match args.workload_generator:
        case WorkloadGenerator.FMPERF:
            raise NotImplementedError('Workload generator not yet supported')
        case WorkloadGenerator.GUIDELLM:
            import_guidellm(args.results_path).print_yaml()
        case WorkloadGenerator.INFERENCE_PERF:
            raise NotImplementedError('Workload generator not yet supported')
        case WorkloadGenerator.VLLM_BENCHMARK:
            import_vllm_benchmark(args.results_path).print_yaml()
        case _:
            sys.stderr.write('Unsupported workload generator: %s\n' %
                args.workload_generator)
            sys.stderr.write('Must be one of: %s\n' %
                str([wg.value for wg in WorkloadGenerator])[1:-1])
            sys.exit(1)
