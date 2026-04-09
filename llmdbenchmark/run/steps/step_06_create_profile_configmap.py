"""Step 05 -- Create ConfigMaps for workload profiles and harness scripts."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext

# ConfigMap name used by the harness pod template (20_harness_pod.yaml.j2).
HARNESS_SCRIPTS_CONFIGMAP = "llmdbench-harness-scripts"


class CreateProfileConfigmapStep(Step):
    """Create ConfigMaps for workload profiles and harness scripts."""

    def __init__(self):
        super().__init__(
            number=6,
            name="create_profile_configmap",
            description="Create profile and harness-scripts ConfigMaps",
            phase=Phase.RUN,
            per_stack=True,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        prologue = self.start(context, stack_path)
        if isinstance(prologue, StepResult):
            return prologue
        cmd = prologue.cmd
        plan_config = prologue.plan_config
        errors = prologue.errors
        stack_name = prologue.stack_name

        # Resolve harness name
        harness_name = self._resolve(
            plan_config, "harness.name",
            context_value=context.harness_name, default="inference-perf",
        )

        # Resolve namespace
        harness_ns = self._resolve(
            plan_config, "harness.namespace", "namespace.name",
            context_value=context.harness_namespace or context.namespace,
        )
        if not harness_ns:
            return self.failure_result(
                "No harness namespace configured",
                ["Cannot create ConfigMap without a namespace"],
                stack_name=stack_name,
                log_errors=False,
            )

        if context.dry_run:
            return self._dry_run(context, harness_name, harness_ns, stack_name)

        profile_ok, profile_msg = self._create_profiles_configmap(
            context, cmd, harness_name, harness_ns,
        )
        if not profile_ok:
            errors.append(profile_msg)

        scripts_ok, scripts_msg = self._create_harness_scripts_configmap(
            context, cmd, harness_ns,
        )
        if not scripts_ok:
            errors.append(scripts_msg)

        if errors:
            return self.failure_result(
                "Failed to create one or more ConfigMaps",
                errors,
                stack_name=stack_name,
                log_errors=False,
            )

        return self.success_result(
            f"ConfigMaps created (profiles + harness-scripts) "
            f"in ns={harness_ns}",
            stack_name=stack_name,
        )

    def _create_profiles_configmap(
        self, context, cmd, harness_name: str, harness_ns: str,
    ) -> tuple[bool, str]:
        """Create the {harness_name}-profiles ConfigMap."""
        configmap_name = f"{harness_name}-profiles"
        profiles_dir = context.workload_profiles_dir() / harness_name

        if not profiles_dir.is_dir() or not any(profiles_dir.iterdir()):
            return False, (
                f"No rendered profiles found in {profiles_dir}. "
                f"Run step 04 (render_profiles) first."
            )

        # Build --from-file args for each profile
        from_file_args: list[str] = []
        profile_count = 0
        for profile_file in sorted(profiles_dir.iterdir()):
            if profile_file.is_file():
                from_file_args.append(
                    f"--from-file={profile_file.name}={profile_file}",
                )
                profile_count += 1

        if profile_count == 0:
            return False, f"No profile files in {profiles_dir}"

        context.logger.log_info(
            f"Creating ConfigMap '{configmap_name}' with "
            f"{profile_count} profile(s) in ns={harness_ns}..."
        )

        ok, msg = self._kubectl_create_configmap(
            cmd, configmap_name, from_file_args, harness_ns, context,
        )
        if ok:
            context.logger.log_info(
                f"ConfigMap '{configmap_name}' created with "
                f"{profile_count} profile(s)"
            )
        return ok, msg

    def _create_harness_scripts_configmap(
        self, context, cmd, harness_ns: str,
    ) -> tuple[bool, str]:
        """Create the llmdbench-harness-scripts ConfigMap from workload/harnesses/."""
        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        harnesses_dir = base_dir / "workload" / "harnesses"

        if not harnesses_dir.is_dir():
            return False, (
                f"Harness scripts directory not found: {harnesses_dir}"
            )

        from_file_args: list[str] = []
        script_count = 0
        for script_file in sorted(harnesses_dir.iterdir()):
            if script_file.is_file():
                from_file_args.append(
                    f"--from-file={script_file.name}={script_file}",
                )
                script_count += 1

        if script_count == 0:
            return False, f"No harness scripts found in {harnesses_dir}"

        context.logger.log_info(
            f"Creating ConfigMap '{HARNESS_SCRIPTS_CONFIGMAP}' with "
            f"{script_count} harness script(s) in ns={harness_ns}..."
        )

        ok, msg = self._kubectl_create_configmap(
            cmd, HARNESS_SCRIPTS_CONFIGMAP, from_file_args, harness_ns,
            context,
        )
        if ok:
            context.logger.log_info(
                f"ConfigMap '{HARNESS_SCRIPTS_CONFIGMAP}' created with "
                f"{script_count} script(s)"
            )
        return ok, msg

    @staticmethod
    def _kubectl_create_configmap(
        cmd, name: str, from_file_args: list[str],
        namespace: str, context,
    ) -> tuple[bool, str]:
        """Create a ConfigMap via kubectl create --dry-run | kubectl apply."""
        cm_yaml_path = context.run_dir() / f"{name}.yaml"

        result = cmd.kube(
            "create", "configmap", name,
            *from_file_args,
            "--namespace", namespace,
            "--dry-run=client", "-o", "yaml",
            check=False,
        )
        if not result.success:
            return False, (
                f"Failed to generate ConfigMap '{name}' YAML: {result.stderr}"
            )

        cm_yaml_path.write_text(result.stdout, encoding="utf-8")

        result = cmd.kube(
            "apply", "-f", str(cm_yaml_path),
            "--namespace", namespace,
            check=False,
        )
        if not result.success:
            return False, (
                f"Failed to apply ConfigMap '{name}': {result.stderr}"
            )

        return True, f"ConfigMap '{name}' created"

    def _dry_run(
        self, context, harness_name: str, harness_ns: str, stack_name: str,
    ) -> StepResult:
        """Handle --dry-run mode."""
        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        harnesses_dir = base_dir / "workload" / "harnesses"
        script_count = (
            sum(1 for f in harnesses_dir.iterdir() if f.is_file())
            if harnesses_dir.is_dir() else 0
        )
        return self.success_result(
            f"[DRY RUN] Would create ConfigMaps: "
            f"'{harness_name}-profiles' and "
            f"'{HARNESS_SCRIPTS_CONFIGMAP}' ({script_count} scripts) "
            f"in ns={harness_ns}",
            stack_name=stack_name,
        )
