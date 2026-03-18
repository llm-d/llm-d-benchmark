"""Step 09 -- Upload results to cloud storage (GCS/S3) if configured.

Acts as a safety-net bulk upload.  Per-pod uploads happen in step_06
during result collection; this step re-uploads the entire results
directory to ensure nothing was missed.
"""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.cloud_upload import upload_all_results


class UploadResultsStep(Step):
    """Upload results to cloud storage if configured."""

    def __init__(self):
        super().__init__(
            number=9,
            name="upload_results",
            description="Upload results to cloud storage",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip upload if output is local."""
        return context.harness_output == "local"

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()
        output = context.harness_output
        results_dir = context.run_results_dir()

        if not results_dir.exists() or not any(results_dir.iterdir()):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No results to upload",
            )

        error = upload_all_results(cmd, results_dir, output, context)
        if error:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=error,
                errors=[error],
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Results uploaded to {output}",
        )
