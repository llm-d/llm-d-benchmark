"""Package-wide workspace configuration singleton."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WorkspaceConfig:
    """Workspace directory paths and runtime flags.

    Configured once at startup via ``setup_workspace()`` in cli.py,
    then importable from any module as ``from llmdbenchmark.config import config``.
    """

    workspace: Optional[Path] = None
    plan_dir: Optional[Path] = None
    log_dir: Optional[Path] = None
    verbose: bool = False
    dry_run: bool = False
    telemetry_enabled: bool = False
    telemetry_provider: str = "http"
    telemetry_endpoint: Optional[str] = None
    telemetry_api_key: Optional[str] = None


# Configured in cli.py via setup_workspace()
config = WorkspaceConfig()
