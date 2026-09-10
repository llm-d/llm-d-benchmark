"""Step 00 -- Run-phase preflight checks.

Validates cluster connectivity and output destination reachability before
executing any benchmark work. Also checks harness namespace existence, but
only informationally -- a missing harness namespace is not fatal since
step 02 (harness prep) creates it later in this same run.
"""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class RunPreflightStep(Step):
    """Validate cluster connectivity and run-phase prerequisites."""

    def __init__(self):
        super().__init__(
            number=0,
            name="run_preflight",
            description="Validate cluster and run-phase prerequisites",
            phase=Phase.RUN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip preflight in skip-run mode (collecting results only)."""
        return context.harness_skip_run

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors: list[str] = []

        # Resolve cluster connectivity and rebuild CommandExecutor
        context.resolve_cluster()
        context.rebuild_cmd()
        cmd = context.require_cmd()

        # Log run-phase banner
        context.logger.line_break()
        context.logger.log_info(
            "Starting benchmark run phase",
            emoji="",
        )

        # In run-only mode, namespace validation is optional
        if not context.is_run_only_mode:
            # Verify harness namespace exists
            harness_ns = context.harness_namespace or context.namespace
            if not harness_ns:
                errors.append(
                    "No namespace configured. Set 'namespace.name' in your "
                    "scenario YAML, defaults.yaml, or pass --namespace on the CLI."
                )
            elif not context.dry_run:
                result = cmd.kube(
                    "get",
                    "namespace",
                    harness_ns,
                    check=False,
                )
                if not result.success:
                    # Step 02 (harness prep) creates this namespace later in
                    # this same run -- it no longer has to pre-exist from a
                    # prior standup, so this is informational, not fatal.
                    context.logger.log_info(
                        f"Harness namespace '{harness_ns}' not found -- it "
                        f"will be created by step 02 (harness prep)"
                    )
                else:
                    context.logger.log_info(f"Harness namespace '{harness_ns}' exists")

        # Validate output destination
        output = context.harness_output
        if output == "local":
            results_dir = context.run_results_dir()
            context.logger.log_info(f"Output destination: local ({results_dir})")
        elif output.startswith("gs://"):
            if not context.dry_run:
                result = cmd.execute(
                    f"gsutil ls {output}",
                    check=False,
                    silent=True,
                )
                if not result.success:
                    errors.append(f"GCS destination not reachable: {output}")
                else:
                    context.logger.log_info(f"Output destination: GCS ({output})")
            else:
                context.logger.log_info(f"[DRY RUN] Output destination: GCS ({output})")
        elif output.startswith("s3://"):
            if not context.dry_run:
                result = cmd.execute(
                    f"aws s3 ls {output}",
                    check=False,
                    silent=True,
                )
                if not result.success:
                    errors.append(f"S3 destination not reachable: {output}")
                else:
                    context.logger.log_info(f"Output destination: S3 ({output})")
            else:
                context.logger.log_info(f"[DRY RUN] Output destination: S3 ({output})")

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="[DRY RUN] Preflight checks logged",
            )

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Preflight checks failed",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Preflight checks passed",
        )
