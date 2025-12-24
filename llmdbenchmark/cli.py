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
import sys
import json
from pathlib import Path

from llmdbenchmark import __version__, __package_name__, __package_home__
from llmdbenchmark.config import config
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.utilities.os.filesystem import (
    create_workspace,
    create_sub_dir_workload,
    get_absolute_path,
)
from llmdbenchmark.interface.commands import Command
from llmdbenchmark.interface import plan, standup
from llmdbenchmark.parser.render_specification import RenderSpecification
from llmdbenchmark.exceptions.exceptions import TemplateError
from llmdbenchmark.parser.render_plans import RenderPlans


def setup_workspace(
    workspace_path: Path,
    plan_dir: Path,
    log_dir: Path,
    verbose: bool = False,
    dry_run: bool = False,
) -> None:
    """
    Configure the llmdbenchmark workspace, plan directory, log directory,
    and flags for verbose logging and dry run mode.

    Args:
        workspace_path (Path): Path to the main workspace directory.
        plan_dir (Path): Path to the generated plan directory.
        log_dir (Path): Path to the logs directory.
        verbose (bool, optional): Enable verbose logging. Defaults to False.
        dry_run (bool, optional): Enable dry run mode. Defaults to False.
    """
    config.workspace = workspace_path
    config.plan_dir = plan_dir
    config.log_dir = log_dir
    config.verbose = verbose
    config.dry_run = dry_run


def dispatch_cli(args: argparse.Namespace, logger: logging.Logger) -> None:
    """
    Process CLI arguments and dispatch execution of commands.

    Args:
        args (Namespace): Parsed command-line arguments from argparse.
        logger (logger): Current logging instance
    """

    if args.command in (
        Command.PLAN.value,
        Command.STANDUP.value,
        Command.END_TO_END.value,
    ):

        #
        # This is the source of where all templates, scenarios, and values
        # should be found to be rendered into workspace.
        #
        specification_as_dict = RenderSpecification(
            specification_file=args.specification_file,
            base_dir=args.base_dir,
        ).eval()

        logger.log_info(
            "Specification file rendered and validated successfully.",
            emoji="✅",
        )

        logger.log_debug(
            "Using specification file to fully render templates into complete system stack plans."
        )

        render_plan_errors = RenderPlans(
            template_dir=specification_as_dict["template_dir"]["path"],
            defaults_file=specification_as_dict["values_file"]["path"],
            scenarios_file=specification_as_dict["scenario_file"]["path"],
            output_dir=config.plan_dir,
        ).eval()

        try:
            if render_plan_errors.has_errors:
                error_dump = json.dumps(render_plan_errors.to_dict(), indent=2)
                raise TemplateError(
                    message="Errors occurred while rendering the specification.",
                    context={"\nrender_plan_errors": error_dump},
                )
        except TemplateError as e:
            logger.log_error(f"Rendering failed: {e}")
            sys.exit(1)

    if args.command in (Command.STANDUP.value, Command.END_TO_END.value):
        logger.log_info("STANDUP TODO")

    if args.command == Command.RUN.value:
        logger.log_info("RUN TODO")


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

    Returns:
        None
    """

    parser = argparse.ArgumentParser(
        prog="llmdbenchmark",
        description="Provision and drive experiments for LLM workloads focused on analyzing "
        "the performance of llm-d and vllm inference platform stacks. "
        f"Visit {__package_home__} for more information.",
        epilog=(
            "A command must be supplied. Commands correspond to high-level actions "
            "such as generating plans, provisioning infrastructure, or running experiments "
            "and workloads."
        ),
    )

    parser.add_argument(
        "--workspace",
        "--ws",
        help="Supply a workspace directory for placing "
        "generated items and logs, otherwise the default action is to create a "
        "temporary directory on your system.",
    )

    parser.add_argument(
        "--base-dir",
        "--bd",
        default=".",
        help="Base directory containing templates and scenarios. "
        'The default base directory is the cwd "." - we highly suggest enforcing a '
        'base_dir explicitly. For example: "BASE_DIR/templates", "BASE_DIR/scenarios".',
    )

    parser.add_argument(
        "--specification_file",
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
        "generating YAML and Helm documents.",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging to console."
    )

    parser.add_argument(
        "--version",
        "--ver",
        action="version",
        version=f"{__package_name__}:{__version__}",
        help="Show program's version number and exit.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        title="Commands",
        description="Available commands:",
    )

    plan.add_subcommands(subparsers)
    standup.add_subcommands(subparsers)

    args = parser.parse_args()

    #
    # NOTE: This section could be refactored or wrapped further in the future.
    #
    # We IMPLICITLY append "workspace" if it has not already been done so so that
    # we don't EVER accidentally commit or store in Git. I've added workspace*/ to
    # .gitignore. We can probably handle this better maybe? This is really for those
    # users that are using the repo root - or for developers actively making changes
    # to the library.
    #
    # Create an "overall" workspace to store individual runs, allowing consolidation
    # into a single directory containing multiple runs:
    #
    #   workspace/
    #       sub_dir_run_1/
    #       sub_dir_run_2/
    #
    # If a random workspace is assigned, temporary directories will be created,
    # each containing a single run. This structure allows optional workspace
    # reusability and easier organization of multiple runs.
    #

    overall_workspace = Path(args.workspace)
    if "workspace" not in overall_workspace.name.lower():
        overall_workspace = overall_workspace.with_name(
            f"workspace_{overall_workspace.name}"
        )
    overall_workspace = create_workspace(overall_workspace)
    absolute_overall_workspace_path = get_absolute_path(overall_workspace)

    current_workspace = create_sub_dir_workload(absolute_overall_workspace_path)
    absolute_workspace_path = get_absolute_path(current_workspace)

    absolute_workspace_log_dir = create_sub_dir_workload(
        absolute_workspace_path, "logs"
    )

    absolute_workspace_plan_dir = create_sub_dir_workload(
        absolute_workspace_path, "plan"
    )

    # Sanitize directories and convert all relative (or ~) paths to
    # absolutes
    args.specification_file = get_absolute_path(args.specification_file)
    args.base_dir = get_absolute_path(args.base_dir)

    setup_workspace(
        workspace_path=absolute_workspace_path,
        plan_dir=absolute_workspace_plan_dir,
        log_dir=absolute_workspace_log_dir,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )

    logger = get_logger(config.log_dir, config.verbose, __name__)

    logger.log_info(
        f'Using Package: "{__package_name__}:{__version__}" found at {__package_home__}'
    )

    logger.log_info(
        f'Created Workspace: "{absolute_overall_workspace_path}"',
        emoji="✅",
    )

    logger.log_info(
        f'Created {__package_name__} instance in workspace: "{absolute_workspace_path}"',
        emoji="✅",
    )

    logger.line_break()

    dispatch_cli(args, logger)


if __name__ == "__main__":
    cli()
