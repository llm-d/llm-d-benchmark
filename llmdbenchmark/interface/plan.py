"""CLI definition for the ``plan`` subcommand."""

import argparse
from llmdbenchmark.interface.commands import Command


def add_subcommands(parser: argparse._SubParsersAction):
    """Register the ``plan`` subcommand and its arguments."""
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
