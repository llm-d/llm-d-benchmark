"""Step 12 -- Build the benchmark reports the harness pod did not build.

Every harness analyzes its own results in the pod, so this is only a fallback:
:func:`llmdbenchmark.analysis.run_analysis` does nothing for a result set that
already holds the pod's reports.
"""

from pathlib import Path

from llmdbenchmark.analysis import run_analysis
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class AnalyzeResultsStep(Step):
    """Build missing benchmark reports on the driver."""

    def __init__(self):
        super().__init__(
            number=12,
            name="analyze_results",
            description="Build missing benchmark reports on the driver",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        # Under skip nothing was copied here, so there is nothing to read.
        return context.harness_debug or context.collect_skip

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        harness_name = context.harness_name or "inference-perf"
        results_dir = context.run_results_dir()

        if not results_dir.exists() or not any(results_dir.iterdir()):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No results to analyze",
            )

        if context.dry_run:
            # Log what would be analyzed (local Python, no kubectl commands)
            subdirs = [d.name for d in sorted(results_dir.iterdir()) if d.is_dir()]
            context.logger.log_info(
                f"[DRY RUN] Would run {harness_name} analysis on "
                f"{len(subdirs)} result set(s) in {results_dir}: "
                f"{', '.join(subdirs) or '(none)'}"
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(f"[DRY RUN] Would analyze {len(subdirs)} result set(s)"),
            )

        analyzed = 0
        for result_subdir in sorted(results_dir.iterdir()):
            if not result_subdir.is_dir():
                continue

            err = run_analysis(harness_name, result_subdir, context)
            if err:
                # A warning, not a failure: the run already finished and this
                # step runs on every run.
                context.logger.log_warning(
                    f"Analysis issue in {result_subdir.name}: {err}"
                )
            else:
                analyzed += 1

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Analyzed {analyzed} result set(s)",
        )
