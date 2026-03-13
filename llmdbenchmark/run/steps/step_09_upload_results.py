"""Step 09 -- Upload results to cloud storage (GCS/S3) if configured."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


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

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    f"[DRY RUN] Would upload results from "
                    f"{results_dir} to {output}"
                ),
            )

        context.logger.log_info(
            f"Uploading results to {output}..."
        )

        if output.startswith("gs://"):
            result = cmd.execute(
                f"gsutil -m cp -r {results_dir}/* {output}/",
                check=False,
            )
            if not result.success:
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message=f"GCS upload failed: {result.stderr}",
                    errors=[result.stderr],
                )
            context.logger.log_info(
                f"Results uploaded to {output}"
            )

        elif output.startswith("s3://"):
            result = cmd.execute(
                f"aws s3 sync {results_dir}/ {output}/",
                check=False,
            )
            if not result.success:
                return StepResult(
                    step_number=self.number,
                    step_name=self.name,
                    success=False,
                    message=f"S3 upload failed: {result.stderr}",
                    errors=[result.stderr],
                )
            context.logger.log_info(
                f"Results uploaded to {output}"
            )

        else:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=f"Unknown output destination: {output}",
                errors=[
                    f"Unsupported output format: {output}. "
                    f"Use 'local', 'gs://...', or 's3://...'"
                ],
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Results uploaded to {output}",
        )
