"""
plan.py

Defines the `plan` subcommand for the CLI, which generates the model infrastructure
plan (YAMLs only).
"""

import argparse
from llmdbenchmark.interface.commands import Command


def add_subcommands(parser: argparse._SubParsersAction):
    """
    Add the `plan` subcommand to the given parser.

    This command generates only the plan for the model infrastructure
    without executing any further actions.

    Args:
        parser (argparse._SubParsersAction): The subparsers object returned by
        parser.add_subparsers().
    """
    parser.add_parser(
        Command.PLAN.value,
        description=(
            "The `plan` command generates a complete plan for a model infrastructure. "
            "It produces YAML and Helm manifests required for provisioning, "
            "but does not execute any actions on the cluster. "
            "This is useful for reviewing and validating the plan before deployment. "
            "There are no additional arguments for this command."
        ),
        help="Generate only the plan for the model infrastructure.",
    )
