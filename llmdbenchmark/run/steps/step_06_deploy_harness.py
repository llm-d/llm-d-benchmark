"""Step 06 -- Deploy harness pod(s) for benchmark execution.

Executes treatments **sequentially**: for each treatment, deploy all
parallel pods, wait for completion, collect results, capture logs,
and clean up before moving to the next treatment.  This matches the
original bash behavior and ensures treatments do not compete for
cluster resources.
"""

import base64
import random
import shutil
import string
import time
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext

# Container states that indicate a pod will never succeed.
_CRASH_STATES = {
    "CrashLoopBackOff", "Error", "OOMKilled",
    "CreateContainerConfigError", "ImagePullBackOff",
    "ErrImagePull", "InvalidImageName",
}


def _capture_label_logs(
    cmd, namespace: str, label: str, dest: Path,
    label_name: str, context: ExecutionContext,
) -> None:
    """Capture aggregated logs for all pods matching *label* in *namespace*."""
    result = cmd.kube(
        "logs",
        "--tail=-1",
        "--prefix=true",
        "-l", label,
        "--namespace", namespace,
        check=False,
    )
    if result.success and result.stdout.strip():
        dest.write_text(result.stdout, encoding="utf-8")
        context.logger.log_info(
            f"Captured {label_name} logs → {dest.name}"
        )
    else:
        # Write an empty file so the user knows we tried
        dest.write_text("", encoding="utf-8")
        context.logger.log_info(
            f"No {label_name} pods found (label={label})"
        )


class DeployHarnessStep(Step):
    """Render, deploy, wait, collect, and clean up harness pods per treatment."""

    def __init__(self):
        super().__init__(
            number=6,
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
        harness_name = context.harness_name
        if not harness_name and plan_config:
            harness_name = plan_config.get("harness", {}).get("name")
        harness_name = harness_name or "inference-perf"

        harness_ns = context.harness_namespace or context.namespace
        if not harness_ns and plan_config:
            harness_ns = (
                plan_config.get("harness", {}).get("namespace")
                or plan_config.get("namespace", {}).get("name")
            )

        endpoint_url = context.deployed_endpoints.get(stack_name, "")
        model_name = context.model_name
        if not model_name and plan_config:
            model_name = plan_config.get("model", {}).get("name", "")

        # Determine stack type
        is_standalone = (
            "standalone" in context.deployed_methods
            or plan_config.get("standalone", {}).get("enabled", False)
        )
        stack_type = "vllm-prod" if is_standalone else "llm-d"

        # Resolve model short name used as the llm-d.ai/model label value
        # (e.g. "meta-llama-3-1-8b") for infrastructure log capture.
        model_label: str | None = None
        if plan_config:
            model_label = plan_config.get("model", {}).get("shortName")

        # The namespace where model-serving infrastructure lives
        deploy_namespace = context.namespace
        if not deploy_namespace and plan_config:
            deploy_namespace = plan_config.get("namespace", {}).get("name")

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
        harness_executable = "llm-d-benchmark.sh"
        if plan_config:
            harness_executable = plan_config.get("harness", {}).get(
                "executable", harness_executable
            )

        # Determine experiment profile name
        profile_name = context.harness_profile
        if not profile_name and plan_config:
            profile_name = (
                plan_config.get("harness", {}).get("experimentProfile")
                or plan_config.get("harness", {}).get("profile")
            )
        profile_name = profile_name or "sanity_random.yaml"
        # Strip .in suffix if present
        if profile_name.endswith(".in"):
            profile_name = profile_name[:-3]

        results_dir_prefix = "/requests"
        if plan_config:
            results_dir_prefix = plan_config.get("experiment", {}).get(
                "resultsDir", "/requests"
            )

        # Resolve pod label for label-based kubectl wait
        pod_label = "llmdbench-harness-launcher"
        if plan_config:
            pod_label = plan_config.get("harness", {}).get("podLabel", pod_label)

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
                emoji="🚀",
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

                # Per-pod results directory — each parallel pod writes to
                # its own sub-directory with an _${i} suffix, matching bash.
                results_dir = (
                    f"{results_dir_prefix}/{experiment_id}_{parallel_idx}"
                )

                # Build harness command per pod (results_dir differs)
                if context.harness_debug:
                    harness_command = "sleep infinity"
                else:
                    harness_command = self._build_harness_command(
                        harness_executable=harness_executable,
                        profile_name=pod_profile_name,
                        harness_name=harness_name,
                        results_dir=results_dir,
                    )

                # Build template values by merging plan_config with runtime values
                template_values = dict(plan_config) if plan_config else {}
                template_values.update({
                    "pod_name": pod_name,
                    "harness_command": harness_command,
                    "endpoint_url": endpoint_url,
                    "experiment_id": experiment_id,
                    "results_dir": results_dir,
                    "stack_type": stack_type,
                })

                # Ensure required nested keys exist with defaults
                template_values.setdefault("harness", {})
                template_values["harness"].setdefault("name", harness_name)
                template_values["harness"].setdefault("namespace", harness_ns)
                template_values.setdefault("namespace", {})
                template_values["namespace"].setdefault("name", harness_ns)
                template_values.setdefault("model", {})
                template_values["model"].setdefault("name", model_name or "")
                template_values.setdefault("images", {}).setdefault("benchmark", {})

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
                wait_errors = self._wait_for_treatment(
                    cmd, pod_label, harness_ns, timeout, context
                )
                if wait_errors:
                    errors.extend(wait_errors)

            # --- Phase 3: Collect this treatment's results ---
            if not context.dry_run and not context.harness_debug:
                collect_errors = self._collect_treatment_results(
                    cmd, experiment_id, harness_ns,
                    results_dir_prefix, context,
                    parallelism=parallelism,
                )
                if collect_errors:
                    errors.extend(collect_errors)

            # --- Phase 4: Capture pod logs ---
            if not context.dry_run:
                self._capture_pod_logs(
                    cmd, treatment_pod_names, harness_ns, context,
                    deploy_namespace=deploy_namespace,
                    model_label=model_label,
                )

            # --- Phase 5: Clean up this treatment's pods ---
            if not context.dry_run and not context.harness_debug:
                self._cleanup_treatment_pods(
                    cmd, treatment_pod_names, harness_ns, context,
                )

            # Track experiment ID for upload step
            context.experiment_ids.append(experiment_id)

            elapsed = time.time() - treatment_start
            context.logger.log_info(
                f"[{treatment_idx}/{total_treatments}] Treatment '{treatment_label}' "
                f"complete ({int(elapsed)}s)",
                emoji="✅",
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
    # Per-treatment lifecycle helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wait_for_treatment(
        cmd, pod_label: str, namespace: str, timeout: int,
        context: ExecutionContext,
    ) -> list[str]:
        """Wait for pods to start and then complete using label-based kubectl wait.

        Uses the same two-phase approach as the original bash:
        1. ``kubectl wait --for=condition=Ready=True`` — pods are running
        2. ``kubectl wait --for=condition=ready=False`` — pods have finished

        Returns a list of error strings (empty on success).
        """
        errors: list[str] = []

        # Phase A: Wait for pods to become Ready (running)
        context.logger.log_info(
            f"Waiting for pods (label=app={pod_label}) to start "
            f"(timeout={timeout}s)..."
        )
        result = cmd.kube(
            "wait", "--for=condition=Ready=True",
            "pod", "-l", f"app={pod_label}",
            "--namespace", namespace,
            f"--timeout={timeout}s",
            check=False,
        )
        if not result.success:
            errors.append(
                f"Pods failed to become Ready: {result.stderr.strip()}"
            )
            return errors

        context.logger.log_info("All pods are running")

        # Phase B: Wait for pods to complete (Ready=False after finish)
        context.logger.log_info(
            f"Waiting for pods (label=app={pod_label}) to complete "
            f"(timeout={timeout}s)..."
        )
        result = cmd.kube(
            "wait", f"--timeout={timeout}s",
            "--for=condition=ready=False",
            "pod", "-l", f"app={pod_label}",
            "--namespace", namespace,
            check=False,
        )
        if not result.success:
            errors.append(
                f"Pods did not complete within timeout: {result.stderr.strip()}"
            )
            return errors

        # Check for crash states
        check_result = cmd.kube(
            "get", "pods",
            "-l", f"app={pod_label}",
            "--namespace", namespace,
            "--no-headers",
            check=False,
        )
        if check_result.success and check_result.stdout:
            for state in _CRASH_STATES:
                if state in check_result.stdout:
                    errors.append(
                        f"Found pods in error state. Run: "
                        f"kubectl --namespace {namespace} get pods "
                        f"-l app={pod_label}"
                    )
                    break

        if not errors:
            context.logger.log_info("All pods completed successfully")

        return errors

    @staticmethod
    def _collect_treatment_results(
        cmd, experiment_id: str, namespace: str,
        results_dir_prefix: str, context: ExecutionContext,
        parallelism: int = 1,
    ) -> list[str]:
        """Collect results for a single treatment from the data-access pod.

        When *parallelism* > 1, each parallel pod stores results in a
        separate sub-directory (``<experiment_id>_1``, ``_2``, …) inside the
        PVC, matching the original bash behaviour.  Results are copied to
        per-pod local directories with the same ``_<i>`` suffix.
        """
        errors: list[str] = []

        # Find data-access pod
        result = cmd.kube(
            "get", "pod",
            "-l", "role=llm-d-benchmark-data-access",
            "--namespace", namespace,
            "-o", "jsonpath={.items[0].metadata.name}",
            check=False,
        )
        if not result.success or not result.stdout.strip():
            errors.append(
                f"Data access pod not found in namespace '{namespace}' — "
                f"cannot collect results for {experiment_id}"
            )
            return errors

        data_pod = result.stdout.strip()
        local_results_dir = context.run_results_dir()
        local_analysis_dir = context.run_analysis_dir()

        context.logger.log_info(
            f"Collecting results for {parallelism} pod(s): {experiment_id}..."
        )

        for i in range(1, parallelism + 1):
            # Per-pod paths — matches bash: ${results_dir_prefix}/${suffix}_${i}
            remote_path = (
                f"{namespace}/{data_pod}:"
                f"{results_dir_prefix}/{experiment_id}_{i}"
            )
            local_path = local_results_dir / f"{experiment_id}_{i}"
            local_path.mkdir(parents=True, exist_ok=True)

            cp_result = cmd.kube(
                "cp", "--retries=5",
                remote_path, str(local_path),
                "--namespace", namespace,
                check=False,
            )
            if cp_result.success:
                file_count = sum(1 for f in local_path.rglob("*") if f.is_file())
                if file_count > 0:
                    context.logger.log_info(
                        f"Collected {file_count} file(s) for "
                        f"{experiment_id}_{i}"
                    )
                else:
                    context.logger.log_warning(
                        f"No files collected for {experiment_id}_{i} "
                        f"(directory may be empty)"
                    )

                # Sync analysis sub-directory to dedicated analysis dir.
                # Matches bash condition: dir exists AND not debug AND
                # timeout != 0 (functions.sh line 445).
                analysis_src = local_path / "analysis"
                if (
                    analysis_src.is_dir()
                    and not context.harness_debug
                    and context.harness_wait_timeout != 0
                ):
                    pod_analysis_dir = local_analysis_dir / f"{experiment_id}_{i}"
                    pod_analysis_dir.mkdir(parents=True, exist_ok=True)
                    for item in analysis_src.iterdir():
                        dest = pod_analysis_dir / item.name
                        if item.is_file():
                            shutil.copy2(str(item), str(dest))
                        elif item.is_dir():
                            shutil.copytree(str(item), str(dest), dirs_exist_ok=True)
                    # Remove analysis from results dir (matches bash rsync + rm)
                    shutil.rmtree(str(analysis_src), ignore_errors=True)
                # Upload per-pod results to cloud storage immediately
                # after collection (matches bash per-pod upload_results call).
                if context.harness_output != "local":
                    upload_err = DeployHarnessStep._upload_pod_results(
                        cmd, local_path, context,
                    )
                    if upload_err:
                        errors.append(upload_err)
            else:
                errors.append(
                    f"Failed to copy results for {experiment_id}_{i}: "
                    f"{cp_result.stderr[:200]}"
                )

        return errors

    @staticmethod
    def _capture_pod_logs(
        cmd, pod_names: list[str], namespace: str,
        context: ExecutionContext,
        deploy_namespace: str | None = None,
        model_label: str | None = None,
    ) -> None:
        """Capture logs from harness pods and model-serving infrastructure.

        Matches the original bash ``capture_pod_logs`` function which captures:
        - Harness pod logs (per pod)
        - Pod status snapshot (``kubectl get pods -o wide``)
        - Model-serving pod logs (label ``llm-d.ai/model=<modelid_label>``)
        - EPP pod logs (label ``inferencepool=<modelid_label>-gaie-epp``)
        - IGW pod logs (label ``app.kubernetes.io/component=inference-gateway``)
        """
        log_dir = context.run_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # The namespace where model-serving infra lives (may differ from
        # the harness namespace — bash uses LLMDBENCH_VLLM_COMMON_NAMESPACE).
        infra_ns = deploy_namespace or context.namespace or namespace

        # --- Harness pod logs ---
        for pod_name in pod_names:
            result = cmd.kube(
                "logs", pod_name,
                "--namespace", namespace,
                check=False,
            )
            if result.success and result.stdout:
                log_file = log_dir / f"{pod_name}.log"
                log_file.write_text(result.stdout, encoding="utf-8")
                context.logger.log_info(
                    f"Captured logs for pod '{pod_name}'"
                )
            else:
                context.logger.log_warning(
                    f"Could not capture logs for pod '{pod_name}'"
                )

        # --- Pod status snapshot (Gap 3) ---
        context.logger.log_info(
            f"Capturing pod status in namespace '{infra_ns}'..."
        )
        status_result = cmd.kube(
            "get", "pods", "-o", "wide",
            "--namespace", infra_ns,
            check=False,
        )
        if status_result.success and status_result.stdout:
            status_file = log_dir / "pod_status.txt"
            status_file.write_text(status_result.stdout, encoding="utf-8")
            context.logger.log_info(
                f"Pod status captured to {status_file.name}"
            )

        # --- Infrastructure logs (Gap 2) ---
        # Only attempt if we have a model label to filter on.
        if model_label:
            # Model-serving pods
            _capture_label_logs(
                cmd, infra_ns,
                f"llm-d.ai/model={model_label}",
                log_dir / "modelserving_pods.log",
                "model-serving", context,
            )

            # EPP (Endpoint Picker Pool) pods
            _capture_label_logs(
                cmd, infra_ns,
                f"inferencepool={model_label}-gaie-epp",
                log_dir / "epp_pods.log",
                "EPP", context,
            )

        # IGW (Inference Gateway) pods — no model label needed
        _capture_label_logs(
            cmd, infra_ns,
            "app.kubernetes.io/component=inference-gateway",
            log_dir / "igw_pods.log",
            "IGW", context,
        )

    @staticmethod
    def _cleanup_treatment_pods(
        cmd, pod_names: list[str], namespace: str,
        context: ExecutionContext,
    ) -> None:
        """Delete pods for the current treatment before starting the next."""
        for pod_name in pod_names:
            result = cmd.kube(
                "delete", "pod", pod_name,
                "--namespace", namespace,
                "--ignore-not-found",
                check=False,
            )
            if result.success:
                context.logger.log_info(f"Deleted pod '{pod_name}'")
            else:
                context.logger.log_warning(
                    f"Could not delete pod '{pod_name}': {result.stderr}"
                )

    @staticmethod
    def _upload_pod_results(
        cmd, local_path: Path, context: ExecutionContext,
    ) -> str | None:
        """Upload a single per-pod result directory to cloud storage.

        Matches the bash ``upload_results`` function which is called per-pod
        inside the result collection loop.  The remote path is computed by
        stripping the local results base directory, matching the bash logic::

            remote=$(echo $local | sed "s^$WORK_DIR/results/^^g")
        """
        output = context.harness_output
        if output == "local":
            return None

        # Compute relative path — just the experiment_id_N directory name
        results_base = context.run_results_dir()
        try:
            relative = str(local_path.relative_to(results_base))
        except ValueError:
            relative = local_path.name

        if context.dry_run:
            context.logger.log_info(
                f"[DRY RUN] Would upload {relative} → {output}/{relative}/"
            )
            return None

        if output.startswith("gs://"):
            result = cmd.execute(
                f"gcloud storage cp --recursive "
                f"{local_path}/ {output}/{relative}/",
                check=False,
            )
            if not result.success:
                return (
                    f"GCS upload failed for {relative}: "
                    f"{result.stderr[:200]}"
                )
            context.logger.log_info(
                f"Uploaded {relative} → {output}/{relative}/",
                emoji="☁️",
            )
        elif output.startswith("s3://"):
            result = cmd.execute(
                f"aws s3 cp --recursive "
                f"{local_path}/ {output}/{relative}/",
                check=False,
            )
            if not result.success:
                return (
                    f"S3 upload failed for {relative}: "
                    f"{result.stderr[:200]}"
                )
            context.logger.log_info(
                f"Uploaded {relative} → {output}/{relative}/",
                emoji="☁️",
            )
        else:
            context.logger.log_warning(
                f"Unknown output destination '{output}' — skipping upload "
                f"for {relative}"
            )

        return None

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
    ) -> str:
        """Build the shell command that runs inside the harness pod."""
        parts: list[str] = []

        # Set runtime env vars that the harness script consumes
        parts.append(
            f"export LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR={results_dir}"
        )
        parts.append(
            f"export LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME={profile_name}"
        )

        # Run the harness script (mounted from ConfigMap)
        script_path = f"/workspace/harnesses/{harness_name}-{harness_executable}"
        parts.append(script_path)

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

    @staticmethod
    def _load_plan_config(context: ExecutionContext) -> dict | None:
        """Load plan config from the first rendered stack."""
        rendered_paths = getattr(context, "rendered_stacks", [])
        for stack_path in rendered_paths or []:
            config_file = stack_path / "config.yaml"
            if config_file.exists():
                with open(config_file, encoding="utf-8") as f:
                    return yaml.safe_load(f)
        return None
