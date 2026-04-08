"""Step 00 -- Run-phase preflight checks.

Validates cluster connectivity, harness namespace existence, and output
destination reachability before executing any benchmark work.
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
                    "get", "namespace", harness_ns, check=False,
                )
                if not result.success:
                    errors.append(
                        f"Harness namespace '{harness_ns}' does not exist. "
                        f"Run the standup phase first."
                    )
                else:
                    context.logger.log_info(
                        f"Harness namespace '{harness_ns}' exists"
                    )

        # Validate output destination
        output = context.harness_output
        if output == "local":
            results_dir = context.run_results_dir()
            context.logger.log_info(
                f"Output destination: local ({results_dir})"
            )
        elif output.startswith("gs://"):
            if not context.dry_run:
                result = cmd.execute(
                    f"gsutil ls {output}", check=False, silent=True,
                )
                if not result.success:
                    errors.append(
                        f"GCS destination not reachable: {output}"
                    )
                else:
                    context.logger.log_info(
                        f"Output destination: GCS ({output})"
                    )
            else:
                context.logger.log_info(
                    f"[DRY RUN] Output destination: GCS ({output})"
                )
        elif output.startswith("s3://"):
            if not context.dry_run:
                result = cmd.execute(
                    f"aws s3 ls {output}", check=False, silent=True,
                )
                if not result.success:
                    errors.append(
                        f"S3 destination not reachable: {output}"
                    )
                else:
                    context.logger.log_info(
                        f"Output destination: S3 ({output})"
                    )
            else:
                context.logger.log_info(
                    f"[DRY RUN] Output destination: S3 ({output})"
                )

        if context.dry_run:
            return self.success_result("[DRY RUN] Preflight checks logged")

        if errors:
            return self.failure_result("Preflight checks failed", errors)

        return self.success_result("Preflight checks passed")
