"""Cleanup Step 00 -- Remove what a benchmark run leaves behind in the namespace."""

from dataclasses import dataclass
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.run.steps.step_06_create_profile_configmap import (
    HARNESS_SCRIPTS_CONFIGMAP,
)
from llmdbenchmark.utilities.kube_helpers import (
    delete_pods_by_label,
    delete_pods_by_names,
    find_data_access_pod,
)

# These ConfigMaps are created by name in the standup/run code rather than
# from a scenario key, so they are matched by name here as well (same list
# teardown step_02 and run step_11 use).
BENCHMARK_CONFIGMAPS = [
    "llm-d-benchmark-preprocesses",
    "llm-d-benchmark-run-parameters",
    "llm-d-benchmark-standup-parameters",
    HARNESS_SCRIPTS_CONFIGMAP,
]


@dataclass(frozen=True)
class CleanupTarget:
    """Resource names one rendered stack leaves behind, read from its config.yaml.

    Harness pods and their ConfigMaps render into ``harness.namespace``
    (falling back to ``namespace.name``); the workload PVC, harness service
    and data-access pod render into ``namespace.name``.
    """

    namespace: str
    harness_namespace: str
    pod_label: str
    service: str
    pvc_name: str
    profiles_configmap: str


class CleanupResourcesStep(Step):
    """Delete benchmark leftovers for every rendered stack. Idempotent."""

    def __init__(self):
        super().__init__(
            number=0,
            name="cleanup_resources",
            description="Remove benchmark leftovers (pods, service, ConfigMaps, PVC)",
            phase=Phase.CLEANUP,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "nok8s" in (context.deployed_methods or [])

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()

        targets: list[CleanupTarget] = []
        for rendered_stack in context.rendered_stacks:
            plan_config = self._load_stack_config(rendered_stack)
            if not plan_config:
                continue
            target = self._target_from_config(plan_config)
            # Multi-stack scenarios routinely share a namespace; clean it once.
            if target not in targets:
                targets.append(target)

        if not targets:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No rendered plan config found.",
                errors=["plan config (config.yaml) not found"],
            )

        deleted: list[str] = []
        errors: list[str] = []
        for target in targets:
            self._clean_target(cmd, context, target, deleted, errors)

        namespaces = ", ".join(
            f'"{ns}"'
            for ns in dict.fromkeys(
                ns for t in targets for ns in (t.harness_namespace, t.namespace)
            )
        )
        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message=f"Cleanup finished with {len(errors)} error(s) in {namespaces}",
                errors=errors,
            )
        if deleted:
            message = f"Removed {len(deleted)} resource(s) from {namespaces}"
        else:
            message = f"No benchmark resources found in {namespaces}"
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=message,
        )

    def _target_from_config(self, plan_config: dict) -> CleanupTarget:
        """Read every resource name from the rendered config -- no fallbacks."""
        namespace = self._require_config(plan_config, "namespace", "name")
        return CleanupTarget(
            namespace=namespace,
            harness_namespace=(
                plan_config.get("harness", {}).get("namespace") or namespace
            ),
            pod_label=self._require_config(plan_config, "harness", "podLabel"),
            service=self._require_config(plan_config, "labels", "app"),
            pvc_name=self._require_config(
                plan_config, "storage", "workloadPvc", "name"
            ),
            profiles_configmap=(
                f"{self._require_config(plan_config, 'harness', 'name')}-profiles"
            ),
        )

    def _clean_target(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        target: CleanupTarget,
        deleted: list[str],
        errors: list[str],
    ) -> None:
        harness_ns = target.harness_namespace
        context.logger.log_info(f'Cleaning harness resources in "{harness_ns}"...')
        delete_pods_by_label(cmd, target.pod_label, harness_ns, context)
        for cm_name in [target.profiles_configmap, *BENCHMARK_CONFIGMAPS]:
            self._delete(
                cmd, context, "configmap", cm_name, harness_ns, deleted, errors
            )

        namespace = target.namespace
        context.logger.log_info(f'Cleaning workload resources in "{namespace}"...')
        # The data-access pod mounts the workload PVC; delete it first so the
        # claim's protection finalizer clears instead of leaving it Terminating.
        data_access_pod = find_data_access_pod(cmd, namespace, attempts=1)
        if data_access_pod:
            delete_pods_by_names(cmd, [data_access_pod], namespace, context)
            deleted.append(f"pod/{data_access_pod}")
        self._delete(
            cmd, context, "service", target.service, namespace, deleted, errors
        )

        if context.keep_pvc:
            context.logger.log_info(
                f'Keeping PVC "{target.pvc_name}" (--keep-pvc). It keeps billing '
                "against its storage backend until deleted."
            )
        else:
            self._delete(
                cmd, context, "pvc", target.pvc_name, namespace, deleted, errors
            )

    @staticmethod
    def _delete(
        cmd: CommandExecutor,
        context: ExecutionContext,
        kind: str,
        name: str,
        namespace: str,
        deleted: list[str],
        errors: list[str],
    ) -> None:
        """Delete one named resource; a resource that no longer exists is a no-op."""
        result = cmd.kube(
            "delete",
            kind,
            name,
            "--namespace",
            namespace,
            "--ignore-not-found",
            check=False,
        )
        if not result.success:
            errors.append(f"Failed to delete {kind}/{name}: {result.stderr}")
        elif result.stdout.strip():
            # kubectl only prints "<kind>/<name> deleted" when it removed something.
            context.logger.log_info(f"Deleted {kind}/{name}", emoji="🗑️")
            deleted.append(f"{kind}/{name}")
