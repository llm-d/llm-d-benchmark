import argparse

from datetime import datetime
from pathlib import Path
from typing import Optional

from llmdbenchmark import __version__, __package_name__, __package_home__
from llmdbenchmark.config import config, AUTO_TMP_DIR
from llmdbenchmark.logging.logger import get_logger
from llmdbenchmark.utilities.os.filesystem import (
    create_workspace,
    create_sub_dir_workload,
    get_absolute_path,
)
from llmdbenchmark.interface import standup
from llmdbenchmark.provision.gateway import test_gateway


def cli():
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
        help="Log all commands without executing against compute cluster, while still generating YAML and Helm documents",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging to console."
    )

    subparsers = parser.add_subparsers(dest="command")
    standup.add_subcommands(subparsers)

    args = parser.parse_args()

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

    config.set_paths(
        workspace=absolute_workspace_path,
        log_dir=absolute_workspace_log_dir,
        verbose=args.verbose,
    )
    logger = get_logger(config.log_dir, config.verbose, __name__)

    logger.log_info(
        f'Using Package: "{__package_name__}:{__version__}" found at {__package_home__}'
    )

    logger.log_info(
        f'Created Workspace: "{absolute_workspace_path}"',
        emoji="✅",
    )

    logger.log_debug(
        f'Cannot Create Workspace: "{absolute_workspace_path}"',
    )

    try:
        raise ValueError("Something broke")
    except ValueError as e:
        logger.log_error(f"Operation failed: {e}", exc_info=True)

    logger.log_warning(
        f'Cannot Create Workspace: "{absolute_workspace_path}"',
    )

    logger.log_error(
        f'Cannot Create Workspace: "{absolute_workspace_path}"',
    )

    logger.line_break()

    test_gateway()


if __name__ == "__main__":
    cli()
