"""Step 07 (nok8s) -- Run the benchmark harness as a LOCAL container.

The default DeployHarnessStep launches the load generator as a Kubernetes Pod.
For the no-Kubernetes (nok8s) method there is no cluster, so this step runs the
same harness image as a docker/podman container on the host, pointed at the
local Envoy endpoint (http://localhost:<listenPort>).  Results are written
straight to the host results dir via a bind-mount -- no PVC or data-access pod.

Reuses DeployHarnessStep's static command/name helpers so the in-container
harness invocation matches the pod path exactly.
"""

import time
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep


class DeployHarnessLocalStep(Step):
    """Run harness container(s) locally against the nok8s endpoint."""

    def __init__(self):
        super().__init__(
            number=7,
            name="deploy_harness_local",
            description="Run benchmark harness as a local container (no k8s)",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        if context.harness_skip_run:
            return True
        return "nok8s" not in (context.deployed_methods or [])

    def execute(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return self._fail(None, "No stack path provided for per-stack step")

        stack_name = stack_path.name
        cmd = context.require_cmd()
        plan_config = self._load_stack_config(stack_path)
        runtime = context.container_runtime or "docker"

        harness_name = self._resolve(
            plan_config,
            "harness.name",
            context_value=context.harness_name,
            default="inference-perf",
        )
        model_name = self._resolve(
            plan_config, "model.name", context_value=context.model_name, default=""
        )
        endpoint_url = (
            context.deployed_endpoints.get(stack_name) or context.endpoint_url
        )
        if not endpoint_url:
            return self._fail(stack_path, "No endpoint URL resolved for nok8s run")

        harness_executable = self._resolve(
            plan_config, "harness.executable", default="llm-d-benchmark.sh"
        )
        entrypoint = (plan_config.get("harness") or {}).get(
            "entrypoint", "llm-d-benchmark.sh"
        )
        profile_name = self._resolve(
            plan_config,
            "harness.experimentProfile",
            "harness.profile",
            context_value=context.harness_profile,
            default="sanity_random.yaml",
        )
        if profile_name.endswith(".in"):
            profile_name = profile_name[:-3]

        images = plan_config.get("images", {}).get("benchmark", {})
        image = (
            f"{images.get('repository', 'ghcr.io/llm-d/llm-d-benchmark')}"
            f":{images.get('tag', 'latest')}"
        )
        hf_token_env = (plan_config.get("nok8s") or {}).get(
            "hfTokenEnv", "HUGGING_FACE_HUB_TOKEN"
        )
        deploy_method = ",".join(context.deployed_methods or ["nok8s"])

        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        harnesses_dir = base_dir / "workload" / "harnesses"
        profiles_dir = context.workload_profiles_dir()
        results_dir = context.run_results_dir()
        timeout = context.harness_wait_timeout

        treatments = context.experiment_treatments or [None]
        parallelism = context.harness_parallelism
        errors: list[str] = []

        for treatment in treatments:
            experiment_id = self._experiment_id(harness_name, treatment)
            pod_profile_name = (
                DeployHarnessStep._treatment_profile_name(profile_name, treatment)
                if treatment
                else profile_name
            )

            names: list[str] = []
            for i in range(1, parallelism + 1):
                name = f"{harness_name}-{experiment_id}-{i}".lower()
                exp_results = f"/requests/{experiment_id}_{i}"
                if context.harness_debug:
                    harness_command = "sleep infinity"
                else:
                    harness_command = DeployHarnessStep._build_harness_command(
                        harness_executable=harness_executable,
                        profile_name=pod_profile_name,
                        harness_name=harness_name,
                        results_dir=exp_results,
                        entrypoint=entrypoint,
                        dataset_url=context.dataset_url,
                    )

                # Write the in-container entry script to disk and mount it, to
                # avoid shell-quoting issues with the harness command.
                entry_host = results_dir / f".harness-entry-{name}.sh"
                if not context.dry_run:
                    entry_host.write_text(
                        "#!/bin/sh\n"
                        "for s in /workspace/harnesses/*.sh; do "
                        'cp "$s" /usr/local/bin/ 2>/dev/null; '
                        'chmod +x "/usr/local/bin/$(basename "$s")" 2>/dev/null; '
                        "done\n"
                        f"{harness_command}\n",
                        encoding="utf-8",
                    )

                run_cmd = (
                    f"{runtime} run -d --name {name} --network host "
                    # Stage markers are logged in local time, then compared against
                    # `date -u` scrape timestamps when clipping per-stage series.
                    f"-e TZ=UTC "
                    f"-e LLMDBENCH_RUN_EXPERIMENT_LAUNCHER=1 "
                    f"-e LLMDBENCH_HARNESS_STACK_ENDPOINT_URL={endpoint_url} "
                    f"-e LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod "
                    f"-e LLMDBENCH_DEPLOY_CURRENT_MODEL={model_name} "
                    f"-e LLMDBENCH_DEPLOY_CURRENT_TOKENIZER={model_name} "
                    f"-e LLMDBENCH_HARNESS_NAME={harness_name} "
                    f"-e LLMDBENCH_RUN_EXPERIMENT_ID={experiment_id} "
                    f"-e LLMDBENCH_DEPLOY_METHODS={deploy_method} "
                    f"-e LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX=/requests "
                    # Harness reads the profile from
                    # $LLMDBENCH_RUN_WORKSPACE_DIR/profiles/<harness>/<workload>;
                    # keep it in sync with the profiles bind-mount below.
                    f"-e LLMDBENCH_RUN_WORKSPACE_DIR=/workspace "
                    f"-e {hf_token_env} "
                    f"-v {results_dir}:/requests "
                    f"-v {profiles_dir}:/workspace/profiles "
                    f"-v {harnesses_dir}:/workspace/harnesses:ro "
                    f"-v {entry_host}:/tmp/harness-entry.sh:ro "
                    # Override the image ENTRYPOINT (llm-d-benchmark.sh) so our
                    # setup script runs -- mirrors the k8s pod's command:[sh,-c].
                    f"--entrypoint sh {image} /tmp/harness-entry.sh"
                )
                cmd.execute(f"{runtime} rm -f {name}", check=False)
                result = cmd.execute(run_cmd, check=False)
                if not result.success and not context.dry_run:
                    errors.append(f"Failed to start harness container {name}")
                else:
                    names.append(name)

            context.logger.log_info(
                f"Launched {len(names)} harness container(s) for "
                f"experiment '{experiment_id}' -> {endpoint_url}"
            )

            if not context.dry_run and names:
                # Block until all containers exit (or the wait times out).
                cmd.execute(
                    f"timeout {timeout} {runtime} wait {' '.join(names)}",
                    check=False,
                    force=True,
                )
                for name in names:
                    # In debug mode the container runs 'sleep infinity', so the
                    # wait always times out -- there is no harness status to read.
                    if not context.harness_debug:
                        error = self._exit_status_error(cmd, runtime, name, timeout)
                        if error:
                            errors.append(error)
                    self._capture_and_remove(cmd, runtime, name, results_dir)

            context.experiment_ids.append(experiment_id)

        if errors:
            # Non-fatal in the same sense as the k8s wait step: partial results
            # are already on the host via the /requests bind-mount.
            context.logger.log_warning(
                f"Harness container(s) did not complete cleanly; partial "
                f"results and .log files may still be under {results_dir}"
            )
            return self._fail(stack_path, "; ".join(errors), errors)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Ran {len(context.experiment_ids)} experiment(s) locally "
                f"against {endpoint_url}"
            ),
            stack_name=stack_name,
        )

    # ------------------------------------------------------------------ #
    def _experiment_id(self, harness_name: str, treatment) -> str:
        timestamp = int(time.time())
        rand = DeployHarnessStep._rand_suffix(6)
        tname = treatment.get("name", "") if isinstance(treatment, dict) else ""
        if tname:
            return f"{harness_name}-{tname}-{timestamp}-{rand}"
        return f"{harness_name}-{timestamp}-{rand}"

    def _exit_status_error(self, cmd, runtime, name, timeout) -> str | None:
        """Return an error string if harness container *name* did not exit 0.

        ``<runtime> wait`` prints the exit code(s) to stdout and exits 0 itself,
        so the wait's own status says nothing about the harness. Ask the runtime
        for the container's terminal state instead. Must be called before
        _capture_and_remove(), which removes the container.
        """
        result = cmd.execute(
            f"{runtime} inspect "
            f"-f '{{{{.State.Status}}}} {{{{.State.ExitCode}}}}' {name}",
            check=False,
            force=True,
        )
        fields = (result.stdout or "").strip().split()
        if not result.success or len(fields) != 2:
            return f"Could not read exit status of harness container {name}"

        status, exit_code = fields
        if status != "exited":
            return (
                f"Harness container {name} did not finish within {timeout}s "
                f"(status={status}); raise --wait-timeout or check that "
                f"'{name}.log' shows progress"
            )
        if exit_code != "0":
            return (
                f"Harness container {name} exited {exit_code}; "
                f"see '{name}.log' in the results dir"
            )
        return None

    def _capture_and_remove(self, cmd, runtime, name, results_dir: Path) -> None:
        result = cmd.execute(f"{runtime} logs {name}", check=False, force=True)
        try:
            (results_dir / f"{name}.log").write_text(
                (result.stdout or "") + (result.stderr or ""), encoding="utf-8"
            )
        except OSError:
            pass
        cmd.execute(f"{runtime} rm -f {name}", check=False)

    def _fail(self, stack_path, message, errors=None) -> StepResult:
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=message,
            errors=errors or [message],
            stack_name=stack_path.name if stack_path else None,
        )
