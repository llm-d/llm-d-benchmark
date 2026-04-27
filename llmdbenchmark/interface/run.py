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
        "--stack",
        default=env("LLMDBENCH_STACK"),
        help=(
            "Comma-separated list of stack names to restrict execution to. "
            "Useful in multi-stack scenarios (e.g. guides/multi-model-wva) "
            "to benchmark a single pool without re-deploying. "
            "Endpoint URL auto-resolves for the selected stack - no need to "
            "pass --endpoint-url. Unknown names fail loudly. "
            "Example: --stack qwen3-06b."
        ),
    )
    run_parser.add_argument(
        "--list-endpoints",
        action="store_true",
        default=False,
        help=(
            "Detect and print per-stack endpoint URLs for a deployed "
            "scenario, then exit (no harness pods launched). The output "
            "includes a copy-paste block with ready-to-run `llmdbenchmark` "
            "invocations pre-populated with each stack's --endpoint-url "
            "and --model."
        ),
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
    run_parser.add_argument(
        "--data-access-timeout",
        type=int,
        default=env_int("LLMDBENCH_DATA_ACCESS_TIMEOUT"),
        help="Seconds to wait for the harness data-access pod to become Ready.",
    )

    # Monitoring
    run_parser.add_argument(
        "-f",
        "--monitoring",
        action="store_true",
        default=None,
        help="Enable vLLM /metrics scraping and pod log capture during the run. Without this flag, metrics are not collected.",
    )

    # Pod configuration
    run_parser.add_argument(
        "-q",
        "--serviceaccount",
        default=env("LLMDBENCH_SERVICE_ACCOUNT"),
        help="Service account for harness pods (env: LLMDBENCH_SERVICE_ACCOUNT).",
    )
    run_parser.add_argument(
        "-g",
        "--envvarspod",
        default=env("LLMDBENCH_HARNESS_ENVVARS_TO_YAML"),
        help="Comma-separated list of env var names to propagate into harness pods (env: LLMDBENCH_HARNESS_ENVVARS_TO_YAML).",
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
