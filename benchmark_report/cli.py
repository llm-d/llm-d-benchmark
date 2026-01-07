"""
Command-line interface for converting application native output files to
a standardized benchmark report. This format can then be used for post
processing that is not specialized to a particular harness.

To run, do:
python -m benchmark_report.cli ...

Use the -j option to print a JSON Schema for the benchmark report.
"""

import argparse
import os

import sys

from . import make_json_schema
from .native_to_br import *


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Convert benchmark run data to standard benchmark report format.')
    parser.add_argument(
        'results_file',
        type=str,
        nargs='?',
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
        help=f'Workload generator used, one of: {str([member.value for member in WorkloadGenerator])[1:-1]}')
    parser.add_argument(
        '-i',
        '--index',
        type=int,
        default=None,
        help='Benchmark index to import, for results files containing multiple runs. Default behavior creates benchmark reports for all runs.')
    parser.add_argument(
        '-j',
        '--json-schema',
        type=str,
        nargs='?',
        const='0.1',
        default=None,
        help='Print JSON Schema for Benchmark Report.')


    args = parser.parse_args()

    if args.json_schema:
        # Print JSON Schema and exit
        print(make_json_schema(args.json_schema))
        sys.exit(0)

    if args.results_file is None:
        parser.error('the following arguments are required unless --json-schema is used: results_file')

    if args.output_file and os.path.exists(
            args.output_file) and not args.force:
        sys.stderr.write('Output file already exists: %s\n' % args.output_file)
        sys.exit(1)

    match args.workload_generator:
        case WorkloadGenerator.GUIDELLM:
            if args.index:
                # Generate benchmark report for a specific index
                if args.output_file:
                    import_guidellm(
                        args.results_file,
                        args.index).export_yaml(
                        args.output_file)
                else:
                    import_guidellm(args.results_file, args.index).print_yaml()
            else:
                br_list = import_guidellm_all(args.results_file)
                # Generate reports for all runs
                for ii, br in enumerate(br_list):
                    if args.output_file:
                        # Create a benchmark report file
                        fname, ext = os.path.splitext(args.output_file)
                        output_file = f'{fname}_{ii}{ext}'
                        if os.path.exists(output_file) and not args.force:
                            sys.stderr.write(
                                'Output file already exists: %s\n' %
                                output_file)
                            sys.exit(1)
                        br.export_yaml(output_file)
                    else:
                        # Don't create a file, just print to stdout
                        print(f'# Benchmark {ii + 1} of {len(br_list)}')
                        br.print_yaml()
        case WorkloadGenerator.INFERENCE_PERF:
            if args.output_file:
                import_inference_perf(
                    args.results_file).export_yaml(args.output_file)
            else:
                import_inference_perf(args.results_file).print_yaml()
        case WorkloadGenerator.VLLM_BENCHMARK:
            if args.output_file:
                import_vllm_benchmark(
                    args.results_file).export_yaml(args.output_file)
            else:
                import_vllm_benchmark(args.results_file).print_yaml()
        case WorkloadGenerator.INFERENCE_MAX:
            if args.output_file:
                import_inference_max(
                    args.results_file).export_yaml(args.output_file)
            else:
                import_inference_max(args.results_file).print_yaml()
        case WorkloadGenerator.NOP:
            if args.output_file:
                import_nop(args.results_file).export_yaml(args.output_file)
            else:
                import_nop(args.results_file).print_yaml()
        case _:
            sys.stderr.write('Unsupported workload generator: %s\n' %
                             args.workload_generator)
            sys.stderr.write('Must be one of: %s\n' %
                             str([wg.value for wg in WorkloadGenerator])[1:-1])
            sys.exit(1)


if __name__ == "__main__":
    main()
