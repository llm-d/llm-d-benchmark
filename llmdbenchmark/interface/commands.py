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
        END_TO_END (str): Command to provision, including plan generation,
                          model infrastructure, and run experiments.
        CONFIG_EXPLORER (str): Command that helps find the most cost-effective, optimal
                               configuration for serving models on llm-d based on hardware
                               specification, workload characteristics, and SLO requirements.
    """

    PLAN = "plan"
    STANDUP = "standup"
    RUN = "run"
    END_TO_END = "end_to_end"
    CONFIG_EXPLORER = "config_explorer"
