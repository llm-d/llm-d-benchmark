"""DoE experiment summary — tracks per-treatment results.

Records the outcome of each setup treatment cycle
(standup → run → teardown) and writes a structured
``experiment-summary.yaml`` at the end of the experiment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TreatmentResult:
    """Outcome of a single setup treatment cycle."""

    setup_treatment: str
    status: str = "pending"
    run_treatments_completed: int = 0
    run_treatments_total: int = 0
    error_message: str | None = None
    workspace_dir: str | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for YAML output."""
        d: dict[str, Any] = {
            "setup_treatment": self.setup_treatment,
            "status": self.status,
            "run_treatments": f"{self.run_treatments_completed}/{self.run_treatments_total}",
            "duration_seconds": round(self.duration_seconds, 1),
        }
        if self.workspace_dir:
            d["workspace_dir"] = self.workspace_dir
        if self.error_message:
            d["error"] = self.error_message
        return d


@dataclass
class ExperimentSummary:
    """Aggregate results for a full DoE experiment."""

    experiment_name: str
    total_setup_treatments: int = 0
    total_run_treatments: int = 0
    results: list[TreatmentResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def total_matrix(self) -> int:
        """Total expected runs: setup × run treatments."""
        return self.total_setup_treatments * max(self.total_run_treatments, 1)

    @property
    def succeeded(self) -> int:
        """Number of setup treatments that completed successfully."""
        return sum(1 for r in self.results if r.status == "success")

    @property
    def failed(self) -> int:
        """Number of setup treatments that failed."""
        return sum(1 for r in self.results if r.status.startswith("failed"))

    def record_success(
        self,
        setup_treatment: str,
        run_completed: int,
        run_total: int,
        workspace_dir: str | None = None,
        duration: float = 0.0,
    ) -> None:
        """Record a successful setup treatment cycle."""
        self.results.append(
            TreatmentResult(
                setup_treatment=setup_treatment,
                status="success",
                run_treatments_completed=run_completed,
                run_treatments_total=run_total,
                workspace_dir=workspace_dir,
                duration_seconds=duration,
            )
        )

    def record_failure(
        self,
        setup_treatment: str,
        phase: str,
        error: str,
        run_completed: int = 0,
        run_total: int = 0,
        workspace_dir: str | None = None,
        duration: float = 0.0,
    ) -> None:
        """Record a failed setup treatment cycle (phase: standup/run/teardown)."""
        self.results.append(
            TreatmentResult(
                setup_treatment=setup_treatment,
                status=f"failed_{phase}",
                run_treatments_completed=run_completed,
                run_treatments_total=run_total,
                error_message=error,
                workspace_dir=workspace_dir,
                duration_seconds=duration,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full summary to a dict."""
        elapsed = time.time() - self.start_time
        return {
            "experiment": self.experiment_name,
            "total_setup_treatments": self.total_setup_treatments,
            "total_run_treatments": self.total_run_treatments,
            "total_matrix": self.total_matrix,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "total_duration_seconds": round(elapsed, 1),
            "treatments": [r.to_dict() for r in self.results],
        }

    def write(self, path: Path) -> None:
        """Write the summary to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.to_dict(),
                f,
                default_flow_style=False,
                sort_keys=False,
            )

    def print_table(self, logger: Any) -> None:
        """Print a summary table to the logger."""
        elapsed = time.time() - self.start_time

        W = 62
        logger.log_info("=" * W)
        logger.log_info("  DoE EXPERIMENT SUMMARY")
        logger.log_info("=" * W)
        logger.log_info(f"  Experiment:      {self.experiment_name}")
        logger.log_info(
            f"  Setup treatments: {self.total_setup_treatments}"
        )
        logger.log_info(f"  Run treatments:  {self.total_run_treatments}")
        logger.log_info(f"  Total matrix:    {self.total_matrix}")
        logger.log_info(f"  Duration:        {elapsed:.0f}s")
        logger.log_info("-" * W)

        for result in self.results:
            status_icon = "\u2705" if result.status == "success" else "\u274c"
            runs = f"{result.run_treatments_completed}/{result.run_treatments_total}"
            logger.log_info(
                f"  {status_icon} {result.setup_treatment:<30s} "
                f"{result.status:<18s} runs: {runs}"
            )
            if result.error_message:
                logger.log_info(f"     Error: {result.error_message}")

        logger.log_info("=" * W)
        logger.log_info(
            f"  Result: {self.succeeded}/{len(self.results)} succeeded, "
            f"{self.failed} failed"
        )
        logger.log_info("=" * W)
