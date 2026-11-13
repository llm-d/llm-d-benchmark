"""Step 08 -- Collect results from PVC to local workspace."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.kube_helpers import find_data_access_pod


class CollectResultsStep(Step):
    """Copy results from PVC to local workspace."""

    def __init__(self):
        super().__init__(
            number=9,
            name="collect_results",
            description="Collect results from PVC to local workspace",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip if there is nothing to collect: debug pods, or results already
        local (nok8s bind-mount, --no-pvc, or step 06)."""
        if "nok8s" in (context.deployed_methods or []):
            return True
        # Debug pods run 'sleep infinity' and never write a result set.
        if context.harness_debug:
            return True
        # --no-pvc: results were copied from the harness pods in step 06 and
        # there is no data-access pod to fall back on.
        if context.no_pvc:
            return True
        # An explicit predicate, not the emptiness check below: run_results_dir()
        # creates the dir on access and preflight already calls it.
        if context.collect_skip:
            return True
        results_dir = context.run_results_dir()
        if results_dir.exists() and any(results_dir.iterdir()):
            return True
        return False

    @staticmethod
    def _list_result_dirs(cmd, data_pod, namespace, prefix) -> list[str] | None:
        """Names under ``prefix`` on the PVC, or None when it cannot be listed."""
        ls_result = cmd.kube(
            "exec",
            data_pod,
            "--namespace",
            namespace,
            "--",
            "ls",
            "-1",
            prefix,
            check=False,
        )
        if not ls_result.success:
            return None
        return [d.strip() for d in ls_result.stdout.splitlines() if d.strip()]

    @staticmethod
    def _copy(cmd, data_pod, namespace, prefix, dir_name, local_path, context):
        # Shares step_07's primitive so -z honours --data-collect rather than always
        # copying the whole tree.
        from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep
        from llmdbenchmark.utilities.archive import KEEP_PLAIN

        return DeployHarnessStep._copy_dir_from_pod(
            cmd,
            data_pod,
            namespace,
            f"{prefix}/{dir_name}",
            local_path,
            context,
            fast_collect=context.collect_fast,
            dir_compressed=False,
            members=KEEP_PLAIN if context.collect_results_only else None,
        )

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
                plan_config,
                "harness.namespace",
                "namespace.name",
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
                "No experiment IDs recorded -- attempting to discover results "
                "from data-access pod..."
            )

        # Resolve results dir prefix
        plan_config = self._load_stack_config(stack_path)
        results_dir_prefix = self._resolve(
            plan_config,
            "experiment.resultsDir",
            default="/requests",
        )

        # Find the data-access pod
        data_pod = find_data_access_pod(
            cmd,
            harness_ns,
            attempts=context.data_access_lookup_attempts,
            delay=context.data_access_lookup_delay,
            context=context,
        )
        if not data_pod:
            if context.dry_run:
                data_pod = "<dry-run-data-pod>"
            else:
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
            # Each harness pod writes <experiment_id>_<N> (step 07), so an ID on
            # its own names no directory. Select the dirs carrying it, matched
            # the way step 07's own PVC discovery matches them.
            dir_names: list[str] = []
            pvc_dirs = self._list_result_dirs(
                cmd, data_pod, harness_ns, results_dir_prefix
            )
            if pvc_dirs is None:
                errors.append(f"Could not list {results_dir_prefix} on the PVC")
            else:
                for exp_id in experiment_ids:
                    matches = [d for d in pvc_dirs if exp_id in d]
                    if not matches:
                        context.logger.log_warning(
                            f"No result directories found for {exp_id} under "
                            f"{results_dir_prefix} on the PVC"
                        )
                    dir_names.extend(matches)

            for dir_name in dir_names:
                local_path = local_results_dir / dir_name
                local_path.mkdir(parents=True, exist_ok=True)

                context.logger.log_info(f"Collecting results: {dir_name}...")

                result = self._copy(
                    cmd,
                    data_pod,
                    harness_ns,
                    results_dir_prefix,
                    dir_name,
                    local_path,
                    context,
                )
                if result.success:
                    # Verify non-empty
                    files = list(local_path.rglob("*"))
                    file_count = sum(1 for f in files if f.is_file())
                    if file_count > 0:
                        total_collected += file_count
                        context.logger.log_info(
                            f"Collected {file_count} file(s) for {dir_name}"
                        )
                    else:
                        context.logger.log_warning(
                            f"No files collected for {dir_name} (directory may be empty)"
                        )
                else:
                    errors.append(
                        f"Failed to copy results for {dir_name}: {result.stderr[:200]}"
                    )
        else:
            # Discovery mode -- list what's in the results dir
            dirs = self._list_result_dirs(cmd, data_pod, harness_ns, results_dir_prefix)
            if dirs:
                context.logger.log_info(f"Found {len(dirs)} result directories on PVC")
                for dir_name in dirs:
                    local_path = local_results_dir / dir_name
                    local_path.mkdir(parents=True, exist_ok=True)

                    result = self._copy(
                        cmd,
                        data_pod,
                        harness_ns,
                        results_dir_prefix,
                        dir_name,
                        local_path,
                        context,
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
            f"Collected {total_collected} total file(s) to {local_results_dir}"
        )
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(f"Collected {total_collected} file(s) for {stack_name}"),
            stack_name=stack_name,
        )

    # _find_data_access_pod is now provided by kube_helpers.find_data_access_pod
