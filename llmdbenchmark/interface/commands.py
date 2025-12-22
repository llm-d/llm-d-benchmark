"""
commands.py

Defines the valid CLI commands for the llmdbenchmark package.

This module provides the `Command` enumeration, which centralizes all
subcommand names used by the CLI.
"""

from enum import Enum


class Command(Enum):
    """
    Enumeration of valid CLI commands for llmdbenchmark.

    Attributes:
        PLAN (str): Command to generate only the plan for model infrastructure.
        STANDUP (str): Command to provision, including plan generation, model infrastructure.
        RUN (str): Command to run a workload against an existing model infrastructure.
        END_TO_END (str): Command to Command to provision, including plan generation,
                          model infrastructure, and run experiments.
    """

    PLAN = "plan"
    STANDUP = "standup"
    RUN = "run"
    END_TO_END = "end_to_end"
