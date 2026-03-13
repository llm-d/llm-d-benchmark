"""Step 03 -- Validate cluster resources and configure workload monitoring."""

from __future__ import annotations

import json
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.utilities.capacity_validator import run_capacity_planner


class WorkloadMonitoringStep(Step):
    """Validate cluster resources and configure user workload monitoring."""

    def __init__(self):
        super().__init__(
            number=3,
            name="workload_monitoring",
            description="Validate cluster resources and configure monitoring",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return context.non_admin

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        cmd = context.require_cmd()
        errors: list[str] = []

        plan_config = self._load_plan_config(context)
        if plan_config:
            self._load_resource_config(context, plan_config)

        is_local = context.is_kind or context.is_minikube
        if is_local:
            context.logger.log_info(
                f"Local cluster ({context.platform_type}) detected — "
                "skipping resource validation and capacity planning"
            )
        else:
            self._validate_accelerator(cmd, context, errors)
            self._validate_network(cmd, context, plan_config, errors)
            self._validate_node_selectors(cmd, context, plan_config, errors)
            self._capacity_planner_sanity_check(cmd, context, plan_config, errors)

        if context.is_openshift and self._is_modelservice(context):
            self._apply_monitoring(cmd, context, errors)

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Resource validation / monitoring had errors",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Cluster resources validated and monitoring configured",
        )

    @staticmethod
    def _is_modelservice(context: ExecutionContext) -> bool:
        """Check if the deployment includes modelservice."""
        return "modelservice" in getattr(context, "deployed_methods", [])

    def _load_resource_config(
        self, context: ExecutionContext, plan_config: dict
    ) -> None:
        """Store accelerator and network resource values from plan config onto the context."""
        accel_resource = plan_config.get("accelerator", {}).get("resource", "")
        if accel_resource:
            context.accelerator_resource = accel_resource
            context.logger.log_info(f"Accelerator resource from plan: {accel_resource}")

        net_resource = plan_config.get("vllmCommon", {}).get("networkResource", "")
        if net_resource:
            context.network_resource = net_resource
            context.logger.log_info(f"Network resource from plan: {net_resource}")

    @staticmethod
    def _get_node_capacity(cmd: CommandExecutor, resource_key: str) -> tuple[int, int]:
        """Query node capacity for a resource key, returning (total, node_count)."""
        result = cmd.kube("get", "nodes", "-o", "json")
        if not result.success or not result.stdout.strip():
            return 0, 0

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return 0, 0

        total = 0
        node_count = 0
        for node in data.get("items", []):
            capacity = node.get("status", {}).get("capacity", {})
            val = capacity.get(resource_key)
            if val is not None:
                try:
                    total += int(val)
                    node_count += 1
                except (ValueError, TypeError):
                    pass

        return total, node_count

    def _validate_accelerator(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ) -> None:
        """Validate that the declared accelerator resource exists on cluster nodes."""
        if context.dry_run:
            if context.accelerator_resource:
                context.logger.log_info(
                    f"[DRY RUN] Would validate accelerator resource: "
                    f"{context.accelerator_resource}"
                )
            return

        if not context.accelerator_resource:
            context.logger.log_info(
                "No accelerator resource configured — "
                "pods will not request GPU resources"
            )
            return

        total, node_count = self._get_node_capacity(cmd, context.accelerator_resource)
        if total > 0:
            context.logger.log_info(
                f"Accelerator {context.accelerator_resource}: "
                f"{total} total across {node_count} node(s)"
            )
        else:
            errors.append(
                f"Accelerator resource '{context.accelerator_resource}' "
                "declared in plan but no capacity found on any cluster node"
            )

    def _validate_network(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict | None,
        errors: list,
    ) -> None:
        """Validate that the declared network resource exists on cluster nodes."""
        if context.dry_run:
            return

        if not context.network_resource:
            context.logger.log_info("No RDMA/IB network resource configured")
            return

        total, node_count = self._get_node_capacity(cmd, context.network_resource)
        if total > 0:
            context.logger.log_info(
                f"Network resource {context.network_resource}: "
                f"{total} total across {node_count} node(s)"
            )
        else:
            errors.append(
                f"Network resource '{context.network_resource}' "
                "declared in plan but no capacity found on any cluster node"
            )

    def _validate_node_selectors(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict | None,
        errors: list,
    ) -> None:
        """Validate that node selector labels from the plan config exist on cluster nodes."""
        if context.dry_run:
            context.logger.log_info("[DRY RUN] Would validate node selector labels")
            return

        if not plan_config:
            return

        selectors: list[tuple[str, str, str]] = []  # (source, key, value)

        affinity = plan_config.get("affinity", {})
        if affinity.get("enabled") and isinstance(affinity.get("nodeSelector"), dict):
            for key, value in affinity["nodeSelector"].items():
                selectors.append(("affinity.nodeSelector", key, str(value)))

        for method in ("standalone", "decode", "prefill"):
            method_config = plan_config.get(method, {})
            if not method_config:
                continue

            ns = method_config.get("nodeSelector")
            if isinstance(ns, dict):
                for key, value in ns.items():
                    selectors.append((f"{method}.nodeSelector", key, str(value)))

            accel_type = method_config.get("acceleratorType", {})
            label_key = accel_type.get("labelKey", "")
            label_value = accel_type.get("labelValue", "")
            if label_key and label_value:
                selectors.append((f"{method}.acceleratorType", label_key, label_value))

        if not selectors:
            return

        node_labels = self._get_all_node_labels(cmd, context)
        if node_labels is None:
            # kubectl call failed — warn but don't block
            context.logger.log_warning(
                "Could not retrieve node labels — " "skipping node selector validation"
            )
            return

        for source, key, value in selectors:
            if self._label_exists_on_nodes(node_labels, key, value):
                context.logger.log_info(
                    f"Node selector {key}={value} ({source}) — " "matched on cluster"
                )
            else:
                errors.append(
                    f"Node selector label '{key}={value}' "
                    f"(from {source}) not found on any cluster node. "
                    "Pods using this selector will be stuck in Pending."
                )

    def _get_all_node_labels(
        self, cmd: CommandExecutor, context: ExecutionContext
    ) -> list[dict[str, str]] | None:
        """Fetch labels from all nodes, or None if kubectl fails."""
        result = cmd.kube(
            "get",
            "nodes",
            "-o",
            "jsonpath={.items[*].metadata.labels}",
        )
        if not result.success:
            return None

        raw = result.stdout.strip()
        if not raw:
            return []

        labels_list: list[dict[str, str]] = []
        for chunk in raw.split("} {"):
            chunk = chunk.strip()
            if not chunk.startswith("{"):
                chunk = "{" + chunk
            if not chunk.endswith("}"):
                chunk = chunk + "}"
            try:
                labels_list.append(json.loads(chunk))
            except (json.JSONDecodeError, ValueError):
                continue

        return labels_list

    @staticmethod
    def _label_exists_on_nodes(
        node_labels: list[dict[str, str]], key: str, value: str
    ) -> bool:
        """Check if any node has the given label key with the given value."""
        for labels in node_labels:
            if labels.get(key) == value:
                return True
        return False

    def _capacity_planner_sanity_check(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict | None,
        errors: list,
    ) -> None:
        """Validate vLLM deployment using the model-aware capacity planner."""
        if context.dry_run:
            context.logger.log_info("[DRY RUN] Would run capacity planner validation")
            return

        if not plan_config:
            return

        ignore_failures = plan_config.get("control", {}).get(
            "ignoreFailedValidation", True
        )

        diagnostics = run_capacity_planner(
            plan_config,
            logger=context.logger,
            ignore_failures=ignore_failures,
        )

        # When ignore_failures is True, diagnostics are tagged WARNING not ERROR
        for diag in diagnostics:
            if "ERROR:" in diag:
                errors.append(diag)

    def _apply_monitoring(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ) -> None:
        """Apply monitoring configuration on OpenShift modelservice clusters."""
        monitoring_yaml = self._find_rendered_yaml(
            context, "03_cluster-monitoring-config"
        )

        if not monitoring_yaml:
            errors.append(
                "Monitoring configuration template "
                "'03_cluster-monitoring-config' not found in rendered plan. "
                "The plan phase should have rendered this file for "
                "modelservice deployments on OpenShift."
            )
            return

        result = cmd.kube("apply", "-f", str(monitoring_yaml))
        if not result.success:
            errors.append(f"Failed to apply monitoring configuration: {result.stderr}")
        else:
            context.logger.log_info("User workload monitoring configured")
