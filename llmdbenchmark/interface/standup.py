"""
standup.py

Defines the `standup` subcommand for the CLI, including argument definitions
and execution configuration for provisioning and standing up model infrastructure.
"""

import argparse
from llmdbenchmark.interface.commands import Command


def add_subcommands(parser: argparse._SubParsersAction):
    """
    Add the `standup` subcommand to the given parser.

    This command provisions and stands up model infrastructure according to the
    specified steps, scenarios, and models. It supports various options for
    Kubernetes deployment, Helm releases, and autoscaling.

    Args:
        parser (argparse._SubParsersAction): The subparsers object returned by
        parser.add_subparsers().
    """
    standup_parser = parser.add_parser(
        Command.STANDUP.value,
        description=(
            "The `standup` command provisions the model infrastructure for a given specification. "
            "It implicitly generates a plan (YAMLs) and then executes the provisioning steps."
        ),
        help="Standup model infrastructure based on given specification.",
    )
    standup_parser.add_argument(
        "-s",
        "--step",
        help="Step list (comma-separated values or ranges, e.g. 0,1,5 or 1-7).",
    )
    standup_parser.add_argument(
        "-c", "--scenario", help="Scenario file to source environment variables from."
    )
    standup_parser.add_argument("-m", "--models", help="List of models to be stood up.")
    standup_parser.add_argument(
        "-p",
        "--namespace",
        help="Namespaces to use (deploy_namespace, benchmark_namespace).",
    )
    standup_parser.add_argument(
        "-t", "--methods", help="Standup methods (standalone, modelservice)."
    )
    standup_parser.add_argument(
        "-a", "--affinity", help="Kubernetes node affinity configuration."
    )
    standup_parser.add_argument(
        "-b", "--annotations", help="Kubernetes pod annotations."
    )
    standup_parser.add_argument(
        "-r", "--release", help="Modelservice Helm chart release name."
    )
    standup_parser.add_argument(
        "-u", "--wva", help="Enable Workload Variant Autoscaler."
    )
