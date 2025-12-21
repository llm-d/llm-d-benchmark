import argparse

from datetime import datetime
from pathlib import Path
from typing import Optional

from llmdbenchmark import __version__, __package_name__, __package_home__

from llmdbenchmark.logging.logger import LLMDBenchmarkLogger

from llmdbenchmark.utilities.os.filesystem import (
    create_tmp_directory,
    create_directory,
    get_absolute_path,
)
from llmdbenchmark.utilities.os.platform import get_user_id
from llmdbenchmark.interface import standup

#############################################################
# Default Internal Globals Scopes to CLI Parser
#############################################################

# TODO: Move these to a constants module

AUTO_TMP_DIR = Path("AUTO_TMP")
PACKAGE_NAME = "llmdbenchmark"


#############################################################
# Workspace Helpers
#############################################################


def create_workspace(workspace_dir: Path) -> Path:
    """
    Create a workspace directory.
    If `workspace_dir` matches AUTO_TMP_DIR, a temporary directory is created.
    Otherwise, ensures the directory exists. We'll need to do this since we allow
    the user to NOT specify a workspace, and we need to actually create one, somewhere.
    """
    if workspace_dir == AUTO_TMP_DIR:
        return create_tmp_directory(suffix=PACKAGE_NAME)
    else:
        create_directory(workspace_dir)
        return workspace_dir


def create_sub_dir_workload(workspace_dir: Path, sub_dir: Optional[str] = None) -> Path:
    """
    Create a subdirectory within the workspace.
    If `sub_dir` is not provided, generates a unique name using user ID and package name.

    This sub_dir within the overall workspace is where the "current" run of llmdbenchmark
    will place materials generated, such as logs and generated values, and reports.
    """
    if not sub_dir:
        prefix = get_user_id()
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        current_workspace = workspace_dir / f"{prefix}-{suffix}"
    else:
        current_workspace = workspace_dir / sub_dir

    create_directory(current_workspace)
    return current_workspace


#############################################################
# CLI Parsing
#############################################################


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

    step = "cli"

    logger = LLMDBenchmarkLogger(absolute_workspace_log_dir, step, args.verbose)

    logger.log_info(
        f'Using Package: "{__package_name__}:{__version__}" found at {__package_home__}'
    )

    logger.log_info(
        f'Created Workspace: "{absolute_workspace_path}"',
        emoji="✅",
    )


if __name__ == "__main__":
    cli()
