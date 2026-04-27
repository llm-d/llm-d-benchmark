"""CLI definition for the ``smoketest`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env, env_int


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``smoketest`` subcommand and its arguments."""
    smoketest_parser = parser.add_parser(
        Command.SMOKETEST.value,
        description=(
            "The `smoketest` command validates a deployed model infrastructure. "
            "It runs health checks, a sample inference request, and per-scenario "
            "configuration validation to ensure the deployment matches expectations."
        ),
        help="Run smoketests against deployed infrastructure.",
    )
    smoketest_parser.add_argument(
        "-s",
        "--step",
        help="Step list (comma-separated values or ranges, e.g. 0,1,2 or 0-2).",
    )
    smoketest_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Namespaces to use (deploy_namespace, benchmark_namespace).",
    )
    smoketest_parser.add_argument(
        "-t", "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Deployment methods (standalone, modelservice, fma).",
    )
    smoketest_parser.add_argument(
        "--parallel",
        type=int,
        default=env_int("LLMDBENCH_PARALLEL", 4),
        help="Max number of stacks to test in parallel (default: 4).",
    )
    smoketest_parser.add_argument(
        "--stack",
        default=env("LLMDBENCH_STACK"),
        help=(
            "Comma-separated list of stack names to restrict execution to. "
            "Useful for re-running the smoketest against a single pool."
        ),
    )
    smoketest_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file for kubectl/helm/helmfile commands.",
    )
