"""Step 11 -- Run local analysis on collected benchmark results.

Delegates to :func:`llmdbenchmark.analysis.run_analysis` which converts
raw harness output into standardised benchmark reports using the bundled
``benchmark_report`` library.
"""

from pathlib import Path

from llmdbenchmark.analysis import run_analysis
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class AnalyzeResultsStep(Step):
    """Run local analysis on collected benchmark results."""

    def __init__(self):
        super().__init__(
            number=12,
            name="analyze_results",
            description="Run local analysis on collected results",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip unless --analyze / LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY=1."""
        if context.harness_debug:
            return True
        return not context.analyze_locally

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
                message=(
                    f"[DRY RUN] Would analyze {len(subdirs)} result set(s)"
                ),
            )

        # Run analysis for each result sub-directory
        errors: list[str] = []
        analyzed = 0

        for result_subdir in sorted(results_dir.iterdir()):
            if not result_subdir.is_dir():
                continue

            context.logger.log_info(
                f"Analyzing {result_subdir.name}...", emoji="🔍"
            )

            err = run_analysis(harness_name, result_subdir, context)
            if err:
                errors.append(err)
            else:
                analyzed += 1

        if errors and analyzed == 0:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="All analysis runs failed",
                errors=errors,
            )

        for err in errors:
            context.logger.log_warning(f"Analysis issue: {err}")

        # Cross-treatment comparison (only useful with 2+ result dirs)
        if analyzed >= 2:
            try:
                from llmdbenchmark.analysis.cross_treatment import (
                    generate_cross_treatment_summary,
                )

                comparison_dir = results_dir / "cross-treatment-comparison"
                compared = generate_cross_treatment_summary(
                    results_dir,
                    output_dir=comparison_dir,
                    context=context,
                )
                if compared:
                    context.logger.log_info(
                        f"Cross-treatment comparison: {compared} treatments "
                        f"compared in {comparison_dir}",
                        emoji="📊",
                    )
            except Exception as exc:
                context.logger.log_warning(
                    f"Cross-treatment comparison failed: {exc}"
                )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Analyzed {analyzed} result set(s)",
        )
