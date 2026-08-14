"""CLI definition for the ``cleanup`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env


def add_subcommands(
    parser: argparse._SubParsersAction, parents: list[argparse.ArgumentParser] = []
):
    """Register the ``cleanup`` subcommand and its arguments."""
    cleanup_parser = parser.add_parser(
        Command.CLEANUP.value,
        parents=parents,
        description=(
            "The `cleanup` command removes the resources a benchmark run "
            "leaves behind: leftover harness launcher pods, the benchmark "
            "ConfigMaps, the data-access pod, the harness service, and the "
            "workload PVC. Resource names are read from the rendered scenario, "
            "so --spec is required. It is idempotent: resources that no longer "
            "exist are skipped, and a namespace with no benchmark resources "
            "exits 0. Pass --keep-pvc to preserve the workload PVC so workload "
            "data survives between runs."
        ),
        help="Remove benchmark leftovers (pods, service, ConfigMaps, PVC) "
        "left by a scenario.",
    )
    cleanup_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Comma-separated namespaces to clean up (model,harness). "
        "Overrides namespace from the plan config. If only one namespace is "
        "provided, it is used for both model and harness.",
    )
    cleanup_parser.add_argument(
        "--keep-pvc",
        action="store_true",
        help="Preserve the workload PVC so workload data survives for the "
        "next run. Everything else is still removed.",
    )
    cleanup_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file for kubectl commands.",
    )
