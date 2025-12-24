"""
workspace_config.py

Singleton-style workspace configuration using only a dataclass.
Provides a single package-wide instance that can be imported and used
across all modules to access the current workspace, plan, and log directories,
as well as verbose and dry_run flags.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorkspaceConfig:
    """
    Stores workspace, plan, and log directory paths with verbose and dry_run flags.

    Attributes:
        workspace (Optional[Path]): Main workspace directory.
        plan_dir (Optional[Path]): Directory for generated plans.
        log_dir (Optional[Path]): Logs directory within the workspace.
        verbose (bool): Enable verbose logging.
        dry_run (bool): Enable dry_run mode.
    """

    workspace: Optional[Path] = None
    plan_dir: Optional[Path] = None
    log_dir: Optional[Path] = None
    verbose: bool = False
    dry_run: bool = False


# Create a SINGLE package-wide instance.
# This instance is configured in llmdbenchmark.cli via setup_workspace().
# Once set, it acts as the single source of truth for the current running instance
# of llmdbenchmark and can be imported throughout the package.
config = WorkspaceConfig()
