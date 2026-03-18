"""Step 08 -- Collect results from PVC to local workspace."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.kube_helpers import find_data_access_pod


class CollectResultsStep(Step):
    """Copy results from PVC to local workspace."""

    def __init__(self):
        super().__init__(
            number=8,
            name="collect_results",
            description="Collect results from PVC to local workspace",
            phase=Phase.RUN,
            per_stack=True,
        )

    # NOTE: This step does NOT override should_skip — it always runs.
    # Collecting results is the purpose of --skip mode.

    def execute(  # pylint: disable=too-many-locals,too-many-branches
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No stack path provided for per-stack step",
                errors=["stack_path is required"],
            )

        stack_name = stack_path.name
        cmd = context.require_cmd()
        errors: list[str] = []

        harness_ns = context.harness_namespace or context.namespace
        if not harness_ns:
            plan_config = self._load_stack_config(stack_path)
            harness_ns = self._resolve(
                plan_config, "harness.namespace", "namespace.name",
            )
        if not harness_ns:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No harness namespace configured for result collection",
                errors=["Cannot collect results without a namespace"],
                stack_name=stack_name,
            )

        experiment_ids = context.experiment_ids
        if not experiment_ids:
            context.logger.log_warning(
                "No experiment IDs recorded — attempting to discover results "
                "from data-access pod..."
            )

        # Resolve results dir prefix
        plan_config = self._load_stack_config(stack_path)
        results_dir_prefix = self._resolve(
            plan_config, "experiment.resultsDir", default="/requests",
        )

        if context.dry_run:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message=(
                    f"[DRY RUN] Would collect results for "
                    f"{len(experiment_ids)} experiment(s)"
                ),
                stack_name=stack_name,
            )

        # Find the data-access pod
        data_pod = find_data_access_pod(cmd, harness_ns)
        if not data_pod:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Data access pod not found",
                errors=[
                    f"No pod with label 'role=llm-d-benchmark-data-access' "
                    f"found in namespace '{harness_ns}'"
                ],
                stack_name=stack_name,
            )

        local_results_dir = context.run_results_dir()
        total_collected = 0

        if experiment_ids:
            # Collect results for each known experiment ID
            for exp_id in experiment_ids:
                remote_path = f"{data_pod}:{results_dir_prefix}/{exp_id}"
                local_path = local_results_dir / exp_id
                local_path.mkdir(parents=True, exist_ok=True)

                context.logger.log_info(
                    f"Collecting results: {exp_id}..."
                )

                result = cmd.kube(
                    "cp", remote_path, str(local_path),
                    namespace=harness_ns,
                    check=False,
                )
                if result.success:
                    # Verify non-empty
                    files = list(local_path.rglob("*"))
                    file_count = sum(1 for f in files if f.is_file())
                    if file_count > 0:
                        total_collected += file_count
                        context.logger.log_info(
                            f"Collected {file_count} file(s) for {exp_id}"
                        )
                    else:
                        context.logger.log_warning(
                            f"No files collected for {exp_id} "
                            f"(directory may be empty)"
                        )
                else:
                    errors.append(
                        f"Failed to copy results for {exp_id}: "
                        f"{result.stderr[:200]}"
                    )
        else:
            # Discovery mode — list what's in the results dir
            ls_result = cmd.kube(
                "exec", data_pod,
                "--namespace", harness_ns,
                "--", "ls", "-1", results_dir_prefix,
                check=False,
            )
            if ls_result.success and ls_result.stdout.strip():
                dirs = ls_result.stdout.strip().split("\n")
                context.logger.log_info(
                    f"Found {len(dirs)} result directories on PVC"
                )
                for dir_name in dirs:
                    dir_name = dir_name.strip()
                    if not dir_name:
                        continue
                    remote_path = f"{data_pod}:{results_dir_prefix}/{dir_name}"
                    local_path = local_results_dir / dir_name
                    local_path.mkdir(parents=True, exist_ok=True)

                    result = cmd.kube(
                        "cp", remote_path, str(local_path),
                        namespace=harness_ns,
                        check=False,
                    )
                    if result.success:
                        file_count = sum(
                            1 for f in local_path.rglob("*") if f.is_file()
                        )
                        total_collected += file_count
                    else:
                        errors.append(
                            f"Failed to copy {dir_name}: {result.stderr[:200]}"
                        )
            else:
                errors.append("No results found on PVC")

        if errors and total_collected == 0:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Failed to collect results",
                errors=errors,
                stack_name=stack_name,
            )

        # Log any non-fatal errors
        for err in errors:
            context.logger.log_warning(f"Collection issue: {err}")

        context.logger.log_info(
            f"Collected {total_collected} total file(s) to "
            f"{local_results_dir}"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Collected {total_collected} file(s) for {stack_name}"
            ),
            stack_name=stack_name,
        )

    # _find_data_access_pod is now provided by kube_helpers.find_data_access_pod
