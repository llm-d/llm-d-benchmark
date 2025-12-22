"""
Configuration module for managing workspace and log directory paths
and verbosity across the llmdbenchmark package.

This module provides a singleton WorkspaceConfig instance that can be imported
and used by submodules to access the current workspace, log directory, and
verbosity flag.
"""

from pathlib import Path
from typing import Optional

AUTO_TMP_DIR = Path("AUTO_TMP")
PACKAGE_NAME = "llmdbenchmark"


class WorkspaceConfig:
    """
    Stores workspace and log directory paths, and verbosity flag, for the current experiment run.

    Attributes:
        workspace (Optional[Path]): Path to the main workspace directory.
        log_dir (Optional[Path]): Path to the logs subdirectory within the workspace.
        verbose (bool): Whether verbose logging is enabled.
    """

    def __init__(self) -> None:
        """
        Initialize a WorkspaceConfig instance with no paths set and verbose disabled.
        """
        self.workspace: Optional[Path] = None
        self.log_dir: Optional[Path] = None
        self.verbose: bool = False

    def set_paths(self, workspace: Path, log_dir: Path, verbose: bool = False) -> None:
        """
        Set the workspace, log directory paths, and verbosity flag.

        Args:
            workspace (Path): The path to the main workspace directory.
            log_dir (Path): The path to the logs directory within the workspace.
            verbose (bool, optional): Enable verbose logging. Defaults to False.
        """
        self.workspace = workspace
        self.log_dir = log_dir
        self.verbose = verbose


# Singleton instance for package-wide use
config = WorkspaceConfig()
