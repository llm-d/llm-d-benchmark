"""CLI definition for the ``run`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env, env_int


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``run`` subcommand and its arguments."""
    run_parser = parser.add_parser(
        Command.RUN.value,
        description=(
            "The `run` command executes benchmark experiments against model infrastructure. "
            "It auto-detects endpoints from stood-up stacks (default) or can target "
            "an existing stack via --endpoint-url or --config."
        ),
        help="Run benchmark experiments against model infrastructure.",
    )

    # Core arguments
    run_parser.add_argument(
        "-s",
        "--step",
        help="Step list (comma-separated values or ranges, e.g. 0,1,3 or 1-4).",
    )
    run_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Namespaces to use (deploy_namespace,benchmark_namespace).",
    )
    run_parser.add_argument(
        "-t",
        "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Deploy method used during standup (standalone, modelservice, or custom resource name).",
    )
    run_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file for kubectl commands.",
    )

    run_parser.add_argument(
        "-m",
        "--model",
        default=env("LLMDBENCH_MODEL"),
        help="Model name override (e.g. facebook/opt-125m). Overrides the value from the plan.",
    )

    # Harness configuration
    run_parser.add_argument(
        "-l",
        "--harness",
        default=env("LLMDBENCH_HARNESS"),
        help="Harness name (inference-perf, guidellm, vllm-benchmark, etc.).",
    )
    run_parser.add_argument(
        "-w",
        "--workload",
        default=env("LLMDBENCH_WORKLOAD"),
        help="Workload profile name (e.g., sanity_random.yaml).",
    )
    run_parser.add_argument(
        "-e",
        "--experiments",
        default=env("LLMDBENCH_EXPERIMENTS"),
        help="Path to experiment treatments YAML for parameter sweeping.",
    )
    run_parser.add_argument(
        "-o",
        "--overrides",
        default=env("LLMDBENCH_OVERRIDES"),
        help="Comma-separated list of workload profile parameter overrides (param=value,...).",
    )
    run_parser.add_argument(
        "-r",
        "--output",
        default=env("LLMDBENCH_OUTPUT"),
        help="Results destination (local path, gs://bucket, s3://bucket).",
    )
    run_parser.add_argument(
        "-j",
        "--parallelism",
        type=int,
        default=env_int("LLMDBENCH_PARALLELISM"),
        help="Number of parallel harness pods to create.",
    )
    run_parser.add_argument(
        "--wait-timeout",
        type=int,
        default=env_int("LLMDBENCH_WAIT_TIMEOUT"),
        help="Seconds to wait for harness completion (0 = do not wait).",
    )
    run_parser.add_argument(
        "-x",
        "--dataset",
        default=env("LLMDBENCH_DATASET"),
        help="URL for dataset to be replayed by the harness.",
    )

    # Mode flags
    run_parser.add_argument(
        "-z",
        "--skip",
        action="store_true",
        help="Skip execution and only collect data from existing results.",
    )
    run_parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Debug mode: start harness pods with 'sleep infinity' instead of running.",
    )
    run_parser.add_argument(
        "--analyze",
        action="store_true",
        default=env("LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY") == "1",
        help="Run local analysis on collected results (env: LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY=1).",
    )

    # Run-only / existing-stack mode
    run_parser.add_argument(
        "-U",
        "--endpoint-url",
        default=env("LLMDBENCH_ENDPOINT_URL"),
        help="Explicit endpoint URL (skips auto-detection; enables run-only mode).",
    )
    run_parser.add_argument(
        "-c",
        "--config",
        dest="run_config",
        help="Path to run config YAML file (enables run-only mode).",
    )
    run_parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Generate a run config YAML from current settings and exit.",
    )
