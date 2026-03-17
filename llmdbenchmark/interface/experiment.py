"""CLI definition for the ``experiment`` subcommand."""

import argparse

from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env, env_int


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``experiment`` subcommand and its arguments."""
    exp_parser = parser.add_parser(
        Command.EXPERIMENT.value,
        description=(
            "The `experiment` command orchestrates a full Design of Experiments (DoE) "
            "lifecycle.  For each setup treatment in the experiment YAML, it renders "
            "plans with config overrides, stands up the stack, runs all run treatments, "
            "and tears down.  Results are collected into an experiment-summary.yaml."
        ),
        help="Run a DoE experiment with automatic standup/run/teardown per setup treatment.",
    )

    exp_parser.add_argument(
        "-e",
        "--experiments",
        required=not env("LLMDBENCH_EXPERIMENTS"),
        default=env("LLMDBENCH_EXPERIMENTS"),
        help="Path to experiment YAML file with setup and run treatments.",
    )

    exp_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Namespaces to use (deploy_namespace,benchmark_namespace).",
    )
    exp_parser.add_argument(
        "-t",
        "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Deploy method (standalone, modelservice).",
    )
    exp_parser.add_argument(
        "-m",
        "--models",
        default=env("LLMDBENCH_MODELS"),
        help="List of models to deploy.",
    )
    exp_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file.",
    )
    exp_parser.add_argument(
        "--parallel",
        type=int,
        default=env_int("LLMDBENCH_PARALLEL", 4),
        help="Max number of stacks to deploy in parallel (default: 4).",
    )
    exp_parser.add_argument(
        "-f",
        "--monitoring",
        action="store_true",
        default=False,
        help="Enable PodMonitor for Prometheus and vLLM /metrics scraping.",
    )

    exp_parser.add_argument(
        "-l",
        "--harness",
        default=env("LLMDBENCH_HARNESS"),
        help="Harness name (inference-perf, guidellm, vllm-benchmark).",
    )
    exp_parser.add_argument(
        "-w",
        "--workload",
        default=env("LLMDBENCH_WORKLOAD"),
        help="Workload profile name (e.g. sanity_random.yaml).",
    )
    exp_parser.add_argument(
        "-o",
        "--overrides",
        default=env("LLMDBENCH_OVERRIDES"),
        help="Additional profile overrides (param=value,...).",
    )
    exp_parser.add_argument(
        "-r",
        "--output",
        default=env("LLMDBENCH_OUTPUT"),
        help="Results destination (local, gs://bucket, s3://bucket).",
    )
    exp_parser.add_argument(
        "-j",
        "--parallelism",
        type=int,
        default=env_int("LLMDBENCH_PARALLELISM"),
        help="Number of parallel harness pods per treatment.",
    )
    exp_parser.add_argument(
        "--wait-timeout",
        type=int,
        default=env_int("LLMDBENCH_WAIT_TIMEOUT"),
        help="Seconds to wait for harness completion (0 = do not wait).",
    )
    exp_parser.add_argument(
        "-x",
        "--dataset",
        default=env("LLMDBENCH_DATASET"),
        help="URL for dataset to be replayed by the harness.",
    )
    exp_parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Debug mode: start harness pods with 'sleep infinity'.",
    )

    exp_parser.add_argument(
        "--stop-on-error",
        action="store_true",
        default=False,
        help="Abort the entire experiment on the first setup treatment failure. "
        "Default behavior is to continue to the next setup treatment.",
    )
    exp_parser.add_argument(
        "--skip-teardown",
        action="store_true",
        default=False,
        help="Skip teardown phase (leave stacks running for debugging).",
    )
