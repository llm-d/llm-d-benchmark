"""Teardown Step 02 -- Remove harness resources (ConfigMaps, pods, secrets)."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class CleanHarnessStep(Step):
    """Remove harness resources (ConfigMaps, pods, secrets)."""

    def __init__(self):
        super().__init__(
            number=2,
            name="clean_harness",
            description="Remove harness resources (ConfigMaps, pods, secrets)",
            phase=Phase.TEARDOWN,
            per_stack=False,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        prologue = self.start(context, stack_path)
        if isinstance(prologue, StepResult):
            return prologue
        cmd = prologue.cmd
        plan_config = prologue.plan_config

        # Use the same context secret name that standup created
        context_secret_name = self._require_config(
            plan_config, "control", "contextSecretName"
        )

        harness_namespaces = self._harness_namespaces(context)

        for harness_ns in harness_namespaces:
            context.logger.log_info(
                f"Cleaning harness resources in namespace \"{harness_ns}\"..."
            )

            self._delete_profile_configmaps(cmd, context, harness_ns)

            context.logger.log_info("  Deleting load generator pods...")
            list_result = cmd.kube(
                "get", "pod",
                "-l", "app=llmdbench-harness-launcher,function=load_generator",
                "--namespace", harness_ns,
                "-o", "name",
                "--ignore-not-found",
            )
            if list_result.success and list_result.stdout.strip():
                pod_names = list_result.stdout.strip().splitlines()
                for pod_name in pod_names:
                    context.logger.log_info(
                        f"    Deleting {pod_name}", emoji="🗑️"
                    )
            cmd.kube(
                "delete", "pod",
                "-l", "app=llmdbench-harness-launcher,function=load_generator",
                "--namespace", harness_ns,
                "--ignore-not-found",
            )

            for cm_name in [
                "llm-d-benchmark-preprocesses",
                "llm-d-benchmark-standup-parameters",
            ]:
                context.logger.log_info(
                    f"    Deleting configmap/{cm_name}", emoji="🗑️"
                )
                cmd.kube(
                    "delete", "configmap", cm_name,
                    "--namespace", harness_ns,
                    "--ignore-not-found",
                )

            context.logger.log_info(
                f"    Deleting secret/{context_secret_name}", emoji="🗑️"
            )
            cmd.kube(
                "delete", "secret", context_secret_name,
                "--namespace", harness_ns,
                "--ignore-not-found",
            )

        ns_list = ", ".join(harness_namespaces)
        return self.success_result(f"Harness resources cleaned (ns={ns_list})")

    def _harness_namespaces(self, context: ExecutionContext) -> list[str]:
        """Collect unique harness namespaces, falling back to context defaults."""
        seen: list[str] = []
        for stack_path in context.rendered_stacks:
            cfg = self._load_stack_config(stack_path)
            if cfg:
                harness_ns = cfg.get("harness", {}).get("namespace")
                if not harness_ns:
                    harness_ns = cfg.get("namespace", {}).get("name")
                if harness_ns and harness_ns not in seen:
                    seen.append(harness_ns)

        fallback = context.harness_namespace or context.require_namespace()
        if not seen:
            seen.append(fallback)
        elif fallback not in seen:
            seen.append(fallback)

        return seen

    def _delete_profile_configmaps(
        self, cmd: CommandExecutor, context: ExecutionContext,
        namespace: str
    ):
        """Delete workload profile ConfigMaps matching the *-profiles pattern."""
        result = cmd.kube(
            "get", "configmap",
            "--namespace", namespace,
            "-o", "jsonpath={.items[*].metadata.name}",
        )
        if not result.success or not result.stdout.strip():
            return

        for cm_name in result.stdout.strip().split():
            if cm_name.endswith("-profiles"):
                context.logger.log_info(
                    f"Deleting profile ConfigMap \"{cm_name}\""
                )
                cmd.kube(
                    "delete", "configmap", cm_name,
                    "--namespace", namespace,
                    "--ignore-not-found",
                )
