"""
cli.py

Entry point for the `llmdbenchmark` command-line interface (CLI).

This module sets up the CLI, parses arguments, and orchestrates workspace
creation, logging, and execution of subcommands.

The CLI allows users to:

- Generate plans for model infrastructure (`plan` command).
- Provision and run experiments (`standup` command).
- Configure workspace directories, logging, and execution options.
- Execute a dry run to generate YAML and Helm manifests without applying them.

Functions:
    cli(): Parses CLI arguments, sets up workspace and logging, and dispatches
            the requested subcommand.
"""

import argparse
import logging

from pathlib import Path

from llmdbenchmark import __version__, __package_name__, __package_home__
from llmdbenchmark.config import config, AUTO_TMP_DIR
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.utilities.os.filesystem import (
    create_workspace,
    create_sub_dir_workload,
    get_absolute_path,
)
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface import plan, standup


def drive_cli_args(args: argparse.Namespace, logger: logging.Logger) -> None:
    """
    Process CLI arguments and dispatch execution of commands.

    Args:
        args (Namespace): Parsed command-line arguments from argparse.
    """

    if args.command == Command.PLAN.value:
        logger.log_info("PLAN")
    elif args.command == Command.STANDUP.value:
        logger.log_info("STANDUP")


def cli() -> None:
    """
    Parse CLI arguments, create workspace, configure logging, and execute
    the requested subcommand.

    Behavior:
        - Sets up the main workspace and subdirectories for runs and logs.
        - Configures the global singleton `config` with workspace, log paths,
          verbosity, and dry-run settings.
        - Initializes a logger for console and file output.
        - Dispatches execution to subcommands defined in `plan` and `standup`.

    CLI Arguments:
        --workspace / --ws (str): Base workspace directory. Defaults to AUTO_TMP_DIR.
        --specification / --spec (str, required): Path to specification file.
        --non-admin / -i (flag): Run as non-cluster-level admin user.
        --dry-run / -n (flag): Generate YAMLs and log commands without execution.
        --verbose / -v (flag): Enable verbose debug logging.
        command (str): Subcommand to execute ('plan', 'standup').

    Returns:
        None
    """
    parser = argparse.ArgumentParser(
        prog="llmdbenchmark",
        description="Provision and Drive Experiments for LLM workloads focused on analyzing"
        "performance of llm-d and vllm inference platforms.",
    )

    parser.add_argument(
        "--workspace",
        "--ws",
        default=f"{AUTO_TMP_DIR}",
        help="Supply a workspace directory for placing "
        "generated items and logs, otherwise the default action is to create a "
        "temporary directory on your system.",
    )

    parser.add_argument(
        "--specification",
        "--spec",
        required=True,
        help="File specifying the experiment (if any), template location, and scenario location. "
        "This file will be used to generate a plan that will be used as part of provisioning, "
        "running experiments, and other actions for this library.",
    )

    parser.add_argument(
        "--non-admin",
        "-i",
        action="store_true",
        help="Run as non-cluster-level admin user.",
    )

    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Log all commands without executing against compute cluster, while still "
        "generating YAML and Helm documents",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging to console."
    )

    subparsers = parser.add_subparsers(dest="command")
    plan.add_subcommands(subparsers)
    standup.add_subcommands(subparsers)

    args = parser.parse_args()

    #
    # TODO: This could be wrapped even further, but leaving here for development
    #
    # Create the "overall" workspace where we will store individual runs
    # so we can consolidate to one directory containing many runs.
    #
    # - workspace
    #   - sub_dir_run_1
    #   - sub_dir_run_1
    #
    # Naturally if a random workspace is assigned, we will consistently create
    # temporary directories containing 1 run per workspace.
    #
    # This structure allows us to have workspace reusability, if so desired.
    #
    workspace = create_workspace(Path(args.workspace))
    current_workspace = create_sub_dir_workload(workspace)
    absolute_workspace_path = get_absolute_path(current_workspace)

    absolute_workspace_log_dir = create_sub_dir_workload(
        absolute_workspace_path, "logs"
    )

    config.set_config(
        workspace=absolute_workspace_path,
        log_dir=absolute_workspace_log_dir,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    logger = get_logger(config.log_dir, config.verbose, __name__)

    logger.log_info(
        f'Using Package: "{__package_name__}:{__version__}" found at {__package_home__}'
    )

    logger.log_info(
        f'Created Workspace: "{absolute_workspace_path}"',
        emoji="✅",
    )

    drive_cli_args(args, logger)


if __name__ == "__main__":
    cli()
