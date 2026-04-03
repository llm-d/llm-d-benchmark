"""Step 06 -- Deploy harness pod(s) for benchmark execution.

Executes treatments **sequentially**: for each treatment, deploy all
parallel pods, wait for completion, collect results, capture logs,
and clean up before moving to the next treatment.  This matches the
original bash behavior and ensures treatments do not compete for
cluster resources.
"""

import base64
import random
import string
import time
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.utilities.kube_helpers import (
    find_data_access_pod,
    wait_for_pods_by_label,
    collect_pod_results,
    sync_analysis_dir,
    delete_pods_by_names,
    capture_pod_logs,
    capture_infrastructure_logs,
)
from llmdbenchmark.utilities.cloud_upload import upload_results_dir


class DeployHarnessStep(Step):
    """Render, deploy, wait, collect, and clean up harness pods per treatment."""

    def __init__(self):
        super().__init__(
            number=7,
            name="deploy_harness",
            description="Deploy harness pod(s) for benchmark execution",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        """Skip deployment in skip-run mode."""
        return context.harness_skip_run

    def execute(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
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
        errors: list[str] = []
        cmd = context.require_cmd()
        plan_config = self._load_stack_config(stack_path)

        # Resolve key configuration
        harness_name = self._resolve(
            plan_config, "harness.name",
            context_value=context.harness_name, default="inference-perf",
        )
        harness_ns = self._resolve(
            plan_config, "harness.namespace", "namespace.name",
            context_value=context.harness_namespace or context.namespace,
        )

        endpoint_url = context.deployed_endpoints.get(stack_name, "")
        model_name = self._resolve(
            plan_config, "model.name",
            context_value=context.model_name, default="",
        )

        # Determine stack type
        is_standalone = (
            "standalone" in context.deployed_methods
            or self._resolve(plan_config, "standalone.enabled", default=False)
        )
        stack_type = "vllm-prod" if is_standalone else "llm-d"

        # Resolve model ID label used as the llm-d.ai/model label value
        # (hashed format matching bash model_attribute) for infrastructure log capture.
        model_label = plan_config.get("model_id_label", "") or self._resolve(plan_config, "model.shortName")

        # The namespace where model-serving infrastructure lives
        deploy_namespace = self._resolve(
            plan_config, "namespace.name",
            context_value=context.namespace,
        )

        # Load the harness pod template
        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        template_path = base_dir / "config" / "templates" / "jinja" / "20_harness_pod.yaml.j2"
        if not template_path.exists():
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Harness pod template not found",
                errors=[f"Expected: {template_path}"],
                stack_name=stack_name,
            )

        # Load macros if present
        macros_path = template_path.parent / "_macros.j2"
        macros_content = ""
        if macros_path.exists():
            macros_content = macros_path.read_text(encoding="utf-8") + "\n"

        template_content = macros_content + template_path.read_text(encoding="utf-8")

        # Resolve harness executable
        harness_executable = self._resolve(
            plan_config, "harness.executable", default="llm-d-benchmark.sh",
        )

        # Determine experiment profile name
        profile_name = self._resolve(
            plan_config, "harness.experimentProfile", "harness.profile",
            context_value=context.harness_profile, default="sanity_random.yaml",
        )
        # Strip .in suffix if present
        if profile_name.endswith(".in"):
            profile_name = profile_name[:-3]

        results_dir_prefix = self._resolve(
            plan_config, "experiment.resultsDir", default="/requests",
        )

        # Resolve pod label for label-based kubectl wait
        pod_label = self._resolve(
            plan_config, "harness.podLabel", default="llmdbench-harness-launcher",
        )

        # Determine treatments and parallelism
        treatments = context.experiment_treatments or [None]
        parallelism = context.harness_parallelism
        timeout = context.harness_wait_timeout

        total_treatments = len(treatments)
        context.logger.log_info(
            f"Running {total_treatments} treatment(s) x {parallelism} "
            f"parallel pod(s) for '{harness_name}' (sequential per treatment)..."
        )

        total_deployed = 0

        for treatment_idx, treatment in enumerate(treatments, 1):
            treatment_start = time.time()

            # Generate experiment ID
            timestamp = int(time.time())
            rand_suffix = self._rand_suffix(6)
            treatment_name = ""
            if treatment and isinstance(treatment, dict):
                treatment_name = treatment.get("name", "")
            if treatment_name:
                experiment_id = f"{harness_name}-{treatment_name}-{timestamp}-{rand_suffix}"
            else:
                experiment_id = f"{harness_name}-{timestamp}-{rand_suffix}"

            treatment_label = treatment_name or "default"
            context.logger.log_info(
                f"[{treatment_idx}/{total_treatments}] Treatment '{treatment_label}': "
                f"deploying {parallelism} pod(s)...",
                emoji="\U0001f680",
            )

            # --- Phase 1: Deploy this treatment's pods ---
            treatment_pod_names: list[str] = []
            deploy_errors: list[str] = []

            # Resolve the treatment-specific profile once (same for all
            # parallel pods within a treatment).
            pod_profile_name = (
                self._treatment_profile_name(profile_name, treatment)
                if treatment else profile_name
            )

            for parallel_idx in range(1, parallelism + 1):
                pod_suffix = self._rand_suffix(8)
                pod_name = f"{harness_name}-{pod_suffix}"

                # Per-pod results directory -- each parallel pod writes to
                # its own sub-directory with an _${i} suffix, matching bash.
                results_dir = (
                    f"{results_dir_prefix}/{experiment_id}_{parallel_idx}"
                )

                # Build harness command per pod (results_dir differs)
                if context.harness_debug:
                    harness_command = "sleep infinity"
                else:
                    harness_cfg = plan_config.get("harness", {}) if plan_config else {}
                    entrypoint = harness_cfg.get(
                        "entrypoint", "llm-d-benchmark.sh"
                    )
                    harness_command = self._build_harness_command(
                        harness_executable=harness_executable,
                        profile_name=pod_profile_name,
                        harness_name=harness_name,
                        results_dir=results_dir,
                        entrypoint=entrypoint,
                    )

                # Build template values by merging plan_config with runtime values
                template_values = dict(plan_config) if plan_config else {}
                # Determine deploy method for benchmark report population
                deploy_method = "modelservice"
                if context.deployed_methods:
                    deploy_method = ",".join(context.deployed_methods)
                elif plan_config:
                    dm = plan_config.get("standalone", {}).get("enabled")
                    if dm:
                        deploy_method = "standalone"

                template_values.update({
                    "pod_name": pod_name,
                    "harness_command": harness_command,
                    "endpoint_url": endpoint_url,
                    "experiment_id": experiment_id,
                    "results_dir": results_dir,
                    "stack_type": stack_type,
                    "deploy_method": deploy_method,
                })

                # Inject base64-encoded kubeconfig so kubectl works inside the pod
                # (needed by collect_metrics.sh and llm-d-benchmark.sh vLLM scraping)
                kubeconfig_path = context.kubeconfig
                if kubeconfig_path and Path(kubeconfig_path).exists():
                    template_values["base64_context_contents"] = self._b64encode_filter(
                        Path(kubeconfig_path).read_text(encoding="utf-8")
                    )

                # Ensure required nested keys exist with defaults
                template_values.setdefault("harness", {})
                template_values["harness"]["name"] = harness_name
                template_values["harness"]["namespace"] = harness_ns
                template_values.setdefault("namespace", {})
                template_values["namespace"]["name"] = harness_ns
                template_values.setdefault("model", {})
                if model_name:
                    template_values["model"]["name"] = model_name
                template_values.setdefault("images", {}).setdefault("benchmark", {})

                # Service account override (-q)
                if context.harness_service_account:
                    template_values["harness"]["serviceAccount"] = context.harness_service_account
                elif plan_config and "serviceAccount" in plan_config:
                    template_values["harness"]["serviceAccount"] = plan_config["serviceAccount"].get("name", "default")

                # Extra env vars to propagate into pod (-g)
                if context.harness_envvars_to_pod:
                    import os
                    extra_env = []
                    for var_name in context.harness_envvars_to_pod.split(","):
                        var_name = var_name.strip()
                        if var_name and var_name in os.environ:
                            extra_env.append({
                                "name": var_name,
                                "value": os.environ[var_name],
                            })
                    if extra_env:
                        template_values["harness"]["extraEnvVars"] = extra_env

                if context.dry_run:
                    context.logger.log_info(
                        f"[DRY RUN] Would deploy pod '{pod_name}' "
                        f"(experiment={experiment_id}, parallel={parallel_idx}/{parallelism})"
                    )
                    treatment_pod_names.append(pod_name)
                    continue

                # Render the template
                try:
                    rendered = self._render_template(template_content, template_values)
                except Exception as exc:
                    deploy_errors.append(
                        f"Failed to render harness pod template: {exc}"
                    )
                    continue

                # Write and apply
                pod_yaml_path = context.run_dir() / f"{pod_name}.yaml"
                pod_yaml_path.write_text(rendered, encoding="utf-8")

                result = cmd.kube(
                    "apply", "-f", str(pod_yaml_path),
                    "--namespace", harness_ns,
                    check=False,
                )
                if not result.success:
                    deploy_errors.append(
                        f"Failed to deploy pod '{pod_name}': {result.stderr}"
                    )
                else:
                    treatment_pod_names.append(pod_name)
                    context.logger.log_info(
                        f"Deployed pod '{pod_name}' "
                        f"(experiment={experiment_id}, "
                        f"parallel={parallel_idx}/{parallelism})"
                    )

            if deploy_errors:
                errors.extend(deploy_errors)

            if not treatment_pod_names:
                context.logger.log_error(
                    f"No pods deployed for treatment '{treatment_label}'"
                )
                continue

            total_deployed += len(treatment_pod_names)

            # --- Phase 2: Wait for this treatment's pods ---
            if not context.dry_run and not context.harness_debug and timeout != 0:
                wait_errors = wait_for_pods_by_label(
                    cmd, pod_label, harness_ns, timeout, context
                )
                if wait_errors:
                    errors.extend(wait_errors)

            # --- Phase 3: Collect this treatment's results ---
            if not context.dry_run and not context.harness_debug:
                collect_errors = self._collect_treatment_results_discovery(
                    cmd, experiment_id, harness_ns,
                    results_dir_prefix, context,
                )
                if collect_errors:
                    errors.extend(collect_errors)

            # --- Phase 4: Capture pod logs (when monitoring is enabled) ---
            monitoring = (plan_config or {}).get("monitoring", {})
            metrics_enabled = str(monitoring.get("metricsScrapeEnabled", False)).lower() == "true"
            if not context.dry_run and metrics_enabled:
                infra_ns = deploy_namespace or context.namespace or harness_ns
                local_results_dir = context.run_results_dir()

                # Capture logs into each parallel pod's results directory,
                # matching the original bash behavior.
                for i in range(1, parallelism + 1):
                    pod_results_dir = local_results_dir / f"{experiment_id}_{i}"
                    pod_log_dir = pod_results_dir / "logs"
                    pod_log_dir.mkdir(parents=True, exist_ok=True)

                    capture_pod_logs(
                        cmd, treatment_pod_names, harness_ns, pod_log_dir, context,
                    )
                    capture_infrastructure_logs(
                        cmd, infra_ns, pod_log_dir, model_label,
                        pod_results_dir, context,
                    )

            # --- Phase 5: Clean up this treatment's pods ---
            if not context.dry_run and not context.harness_debug:
                delete_pods_by_names(
                    cmd, treatment_pod_names, harness_ns, context,
                )

            # Track experiment ID for upload step
            context.experiment_ids.append(experiment_id)

            elapsed = time.time() - treatment_start
            context.logger.log_info(
                f"[{treatment_idx}/{total_treatments}] Treatment '{treatment_label}' "
                f"complete ({int(elapsed)}s)",
                emoji="\u2705",
            )

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Some treatments had errors",
                errors=errors,
                stack_name=stack_name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Completed {total_treatments} treatment(s), "
                f"{total_deployed} pod(s) total for {stack_name}"
            ),
            stack_name=stack_name,
        )

    # ------------------------------------------------------------------
    # Per-treatment result collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_treatment_results_discovery(
        cmd, experiment_id: str, namespace: str,
        results_dir_prefix: str, context: ExecutionContext,
    ) -> list[str]:
        """Collect results by discovering directories on the PVC.

        The entrypoint may construct a results path that differs from
        what step_06 predicted (e.g. old images use a different naming
        convention).  This method lists all directories on the PVC that
        contain the experiment_id and copies them.
        """
        errors: list[str] = []

        data_pod = find_data_access_pod(cmd, namespace)
        if not data_pod:
            errors.append(
                f"Data access pod not found in namespace '{namespace}' -- "
                f"cannot collect results for {experiment_id}"
            )
            return errors

        local_results_dir = context.run_results_dir()
        local_analysis_dir = context.run_analysis_dir()

        # List directories on the PVC under the results prefix
        ls_result = cmd.kube(
            "exec", data_pod,
            "--", "ls", "-1", results_dir_prefix,
            namespace=namespace,
            check=False,
        )
        if not ls_result.success or not ls_result.stdout.strip():
            errors.append(
                f"Could not list results on PVC: {ls_result.stderr[:200]}"
            )
            return errors

        # Find directories matching this experiment
        all_dirs = [
            d.strip() for d in ls_result.stdout.strip().split("\n")
            if d.strip()
        ]
        matching_dirs = [
            d for d in all_dirs if experiment_id in d
        ]

        if not matching_dirs:
            context.logger.log_warning(
                f"No result directories found for experiment {experiment_id} "
                f"on PVC (found: {all_dirs[:5]})"
            )
            return errors

        context.logger.log_info(
            f"Collecting results for {len(matching_dirs)} dir(s): "
            f"{', '.join(matching_dirs)}"
        )

        for dir_name in matching_dirs:
            remote_path = f"{data_pod}:{results_dir_prefix}/{dir_name}"
            local_path = local_results_dir / dir_name
            local_path.mkdir(parents=True, exist_ok=True)

            cp_result = cmd.kube(
                "cp", "--retries=5",
                remote_path, str(local_path),
                namespace=namespace,
                check=False,
            )

            if cp_result.success:
                file_count = sum(1 for f in local_path.rglob("*") if f.is_file())
                context.logger.log_info(
                    f"Collected {file_count} file(s) for {dir_name}"
                )
                # Sync analysis sub-directory
                if not context.harness_debug and context.harness_wait_timeout != 0:
                    sync_analysis_dir(
                        local_path, local_analysis_dir, dir_name,
                    )
            else:
                errors.append(
                    f"Failed to copy {dir_name}: {cp_result.stderr[:200]}"
                )

        return errors

    @staticmethod
    def _collect_treatment_results(
        cmd, experiment_id: str, namespace: str,
        results_dir_prefix: str, context: ExecutionContext,
        parallelism: int = 1,
    ) -> list[str]:
        """Collect results for a single treatment from the data-access pod.

        Uses shared helpers for pod discovery, per-pod copy, analysis sync,
        and per-pod upload.
        """
        errors: list[str] = []

        data_pod = find_data_access_pod(cmd, namespace)
        if not data_pod:
            errors.append(
                f"Data access pod not found in namespace '{namespace}' \u2014 "
                f"cannot collect results for {experiment_id}"
            )
            return errors

        local_results_dir = context.run_results_dir()
        local_analysis_dir = context.run_analysis_dir()

        context.logger.log_info(
            f"Collecting results for {parallelism} pod(s): {experiment_id}..."
        )

        for i in range(1, parallelism + 1):
            pod_suffix = f"{experiment_id}_{i}"

            local_path, success, err_msg = collect_pod_results(
                cmd, data_pod, namespace, results_dir_prefix,
                experiment_id, i, local_results_dir, context,
            )

            if success:
                # Sync analysis sub-directory to dedicated analysis dir.
                # Matches bash condition: dir exists AND not debug AND
                # timeout != 0 (functions.sh line 445).
                if (
                    not context.harness_debug
                    and context.harness_wait_timeout != 0
                ):
                    sync_analysis_dir(
                        local_path, local_analysis_dir, pod_suffix,
                    )
                # Upload per-pod results to cloud storage immediately
                # after collection (matches bash per-pod upload_results call).
                if context.harness_output != "local":
                    upload_err = upload_results_dir(
                        cmd, local_path, context.harness_output, context,
                    )
                    if upload_err:
                        errors.append(upload_err)
            else:
                errors.append(err_msg)

        return errors

    # ------------------------------------------------------------------
    # Template rendering and helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rand_suffix(length: int = 8) -> str:
        """Generate a random lowercase alphanumeric suffix."""
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

    @staticmethod
    def _render_template(template_content: str, values: dict) -> str:
        """Render a Jinja2 template with the harness pod values."""
        env = Environment(
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

        # Register custom filters matching RenderPlans
        env.filters["toyaml"] = DeployHarnessStep._toyaml_filter
        env.filters["is_empty"] = DeployHarnessStep._is_empty_filter
        env.filters["default_if_empty"] = DeployHarnessStep._default_if_empty_filter
        env.filters["b64encode"] = DeployHarnessStep._b64encode_filter

        template = env.from_string(template_content)
        return template.render(**values)

    @staticmethod
    def _toyaml_filter(
        value: Any, indent: int = 0, default_flow_style: bool = False
    ) -> str:
        """Convert Python object to YAML string."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)) and len(value) == 0:
            return ""
        result = yaml.dump(
            value, default_flow_style=default_flow_style, allow_unicode=True
        ).rstrip()
        if indent > 0:
            lines = result.split("\n")
            return "\n".join(
                " " * indent + line if line.strip() else line for line in lines
            )
        return result

    @staticmethod
    def _is_empty_filter(value: Any) -> bool:
        """Check if value is empty."""
        if value is None:
            return True
        if isinstance(value, str) and not value.strip():
            return True
        if isinstance(value, (dict, list)) and len(value) == 0:
            return True
        return False

    @staticmethod
    def _default_if_empty_filter(value: Any, default_value: Any) -> Any:
        """Return default value if value is empty."""
        if DeployHarnessStep._is_empty_filter(value):
            return default_value
        return value

    @staticmethod
    def _b64encode_filter(value: str) -> str:
        """Base64-encode a plain-text string."""
        if not value or not isinstance(value, str):
            return value
        return base64.b64encode(value.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _build_harness_command(
        harness_executable: str,
        profile_name: str,
        harness_name: str,
        results_dir: str,
        entrypoint: str = "llm-d-benchmark.sh",
    ) -> str:
        """Build the shell command that runs inside the harness pod.

        Pre-computes all paths (harness script, analyzer, results dir)
        and exports them before calling the entrypoint.  This matches
        the old ``run.sh`` approach where the workstation is the source
        of truth -- the entrypoint's auto-discovery block (line 56) is
        skipped because ``LLMDBENCH_RUN_EXPERIMENT_HARNESS_NAME_AUTO``
        stays at its default value of ``1``.

        The entrypoint still handles: kubeconfig setup, pre/post vLLM
        metrics scraping, harness execution with retries, and
        in-container analysis.

        The entrypoint is configurable via ``harness.entrypoint`` in
        the scenario YAML (default: ``llm-d-benchmark.sh``).
        """
        # Derive the harness script and analyzer names the same way
        # llm-d-benchmark.sh would (matching its find/grep logic)
        harness_script = f"{harness_name}-{harness_executable}"
        analyzer_script = f"{harness_name}-analyze_results.sh"
        if harness_name == "nop":
            analyzer_script = "nop-analyze_results.py"

        parts: list[str] = []

        # Pre-compute all vars -- entrypoint uses them directly
        parts.append(
            f"export LLMDBENCH_RUN_EXPERIMENT_HARNESS={harness_script}"
        )
        parts.append(
            f"export LLMDBENCH_RUN_EXPERIMENT_ANALYZER={analyzer_script}"
        )
        parts.append(
            f"export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR={results_dir}"
        )
        parts.append(
            f"export LLMDBENCH_CONTROL_WORK_DIR={results_dir}"
        )
        parts.append(
            f"export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME={profile_name}"
        )

        # Capture harness timing and version for benchmark report population
        parts.append(
            "export LLMDBENCH_HARNESS_START=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        )
        parts.append(
            f"export LLMDBENCH_HARNESS_ARGS='--workload {profile_name}'"
        )

        # Extract harness version from repos.txt at runtime (set inside container)
        parts.append(
            f"export LLMDBENCH_HARNESS_VERSION=$(grep '^{harness_name}:' "
            f"/workspace/repos.txt 2>/dev/null | cut -d' ' -f3 || echo 'unknown')"
        )

        # Call the entrypoint without --harness flag so NAME_AUTO stays 1
        # and the auto-discovery block is skipped (our exports are used as-is)
        parts.append(entrypoint)

        return "; ".join(parts)

    @staticmethod
    def _treatment_profile_name(base_name: str, treatment: dict | None) -> str:
        """Generate a treatment-specific profile filename."""
        if not treatment or not isinstance(treatment, dict):
            return base_name
        treatment_name = treatment.get("name", "")
        if not treatment_name:
            return base_name
        stem = Path(base_name).stem
        suffix = Path(base_name).suffix
        return f"{stem}-{treatment_name}{suffix}"

    def _load_plan_config(self, context: ExecutionContext) -> dict | None:
        """Load plan config from the first rendered stack."""
        rendered_paths = getattr(context, "rendered_stacks", [])
        for stack_path in rendered_paths or []:
            config_file = stack_path / "config.yaml"
            if config_file.exists():
                with open(config_file, encoding="utf-8") as f:
                    return yaml.safe_load(f)
        return None
