"""Step 06 -- Deploy harness pod(s) for benchmark execution."""

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


class DeployHarnessStep(Step):
    """Render and deploy harness pod(s) for benchmark execution."""

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

        # Determine treatments
        treatments = context.experiment_treatments or [None]
        parallelism = context.harness_parallelism

        context.logger.log_info(
            f"Deploying {len(treatments)} treatment(s) x {parallelism} "
            f"parallel instance(s) for '{harness_name}'..."
        )

        for treatment in treatments:
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

            context.experiment_ids.append(experiment_id)

            results_dir = f"{results_dir_prefix}/{experiment_id}"

            # Build harness command
            if context.harness_debug:
                harness_command = "sleep infinity"
            else:
                harness_command = self._build_harness_command(
                    harness_executable=harness_executable,
                    profile_name=(
                        self._treatment_profile_name(profile_name, treatment)
                        if treatment else profile_name
                    ),
                    harness_name=harness_name,
                    results_dir=results_dir,
                )

            for parallel_idx in range(1, parallelism + 1):
                pod_suffix = self._rand_suffix(8)
                pod_name = f"{harness_name}-{pod_suffix}"

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
                    context.deployed_pod_names.append(pod_name)
                    continue

                # Render the template
                try:
                    rendered = self._render_template(template_content, template_values)
                except Exception as exc:
                    errors.append(
                        f"Failed to render harness pod template: {exc}"
                    )
                    continue

                # Write and apply
                pod_yaml_path = (
                    context.run_dir() / f"{pod_name}.yaml"
                )
                pod_yaml_path.write_text(rendered, encoding="utf-8")

                result = cmd.kube(
                    "apply", "-f", str(pod_yaml_path),
                    "--namespace", harness_ns,
                    check=False,
                )
                if not result.success:
                    errors.append(
                        f"Failed to deploy pod '{pod_name}': {result.stderr}"
                    )
                else:
                    context.deployed_pod_names.append(pod_name)
                    context.logger.log_info(
                        f"Deployed pod '{pod_name}' "
                        f"(experiment={experiment_id}, "
                        f"parallel={parallel_idx}/{parallelism})"
                    )

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Some harness pods failed to deploy",
                errors=errors,
                stack_name=stack_name,
            )

        total = len(context.deployed_pod_names)
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Deployed {total} harness pod(s) for {stack_name}",
            stack_name=stack_name,
        )

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
