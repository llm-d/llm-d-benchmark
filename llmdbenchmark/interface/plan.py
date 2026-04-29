"""CLI definition for the ``plan`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface.env import env


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``plan`` subcommand and its arguments.

    The plan subcommand renders templates without applying anything to the
    cluster. Flags that influence rendering (namespace, model, deploy method,
    monitoring) are accepted here so the rendered output matches what standup
    would produce with the same flags.
    """
    plan_parser = parser.add_parser(
        Command.PLAN.value,
        description=(
            "The `plan` command generates a complete plan for a model infrastructure. "
            "It produces YAML and Helm manifests required for provisioning, "
            "but does not execute any actions on the cluster. "
            "This is useful for reviewing and validating the plan before deployment."
        ),
        help="Generate only the plan for the model infrastructure.",
    )
    plan_parser.add_argument(
        "-p",
        "--namespace",
        default=env("LLMDBENCH_NAMESPACE"),
        help="Namespaces to use (deploy_namespace, benchmark_namespace).",
    )
    plan_parser.add_argument(
        "-m", "--models",
        default=env("LLMDBENCH_MODELS"),
        help="Model to render the plan for.",
    )
    plan_parser.add_argument(
        "-t", "--methods",
        default=env("LLMDBENCH_METHODS"),
        help="Deployment method (standalone, modelservice, fma).",
    )
    plan_parser.add_argument(
        "-f",
        "--monitoring",
        action="store_true",
        default=False,
        help="Enable monitoring in rendered templates (PodMonitor, EPP verbosity).",
    )
    plan_parser.add_argument(
        "--kubeconfig",
        "-k",
        default=env("LLMDBENCH_KUBECONFIG") or env("KUBECONFIG"),
        help="Path to kubeconfig file (used for cluster resource auto-detection).",
    )
