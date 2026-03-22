"""Validator for the simulated-accelerators well-lit path."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class SimulatedAcceleratorsValidator(BaseSmoketest):
    """Validates simulated accelerator scenario (no real GPUs)."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify no real GPU resources are allocated on any pod role."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] simulated-accelerators config validation skipped",
            ))
            return report

        model_short = _nested_get(config, "model", "shortName") or ""
        model_name = _nested_get(config, "model", "name") or ""

        # Report configured model
        report.add(CheckResult(
            "model_configured",
            bool(model_name),
            expected="model name configured",
            actual=model_name,
            message=f"Model: {model_name}",
        ))

        decode_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role=decode",
        )
        if not decode_pods:
            report.add(CheckResult(
                "no_decode_pods",
                True,
                message="No decode pod(s) -- standalone deployment",
            ))
        if decode_pods:
            pod = decode_pods[0]
            resources = self.get_pod_resources(pod)
            limits = resources.get("limits", {})

            # Scenario-specific: no real GPU resources
            has_gpu = any(
                "gpu" in k.lower() for k in limits
                if k not in ("cpu", "memory", "ephemeral-storage")
            )
            report.add(CheckResult(
                "decode_no_gpu",
                not has_gpu,
                message=f"Decode: {'no' if not has_gpu else 'found'} GPU resources -- simulated accelerator",
            ))

        standalone_enabled = _nested_get(config, "standalone", "enabled")
        if standalone_enabled:
            standalone_role = _nested_get(config, "standalone", "role") or "standalone"
            standalone_pods = self.get_pod_specs(
                cmd, namespace,
                f"llm-d.ai/model={model_short},llm-d.ai/role={standalone_role}",
            )
            report.add(CheckResult(
                "standalone_pods",
                len(standalone_pods) > 0,
                message=f"Found {len(standalone_pods)} standalone pod(s)",
            ))

            if standalone_pods:
                pod = standalone_pods[0]
                # Standalone container name varies (e.g. vllm-standalone-facebook-opt-125m)
                # Use the first container in the pod
                containers = self.get_pod_containers(pod)
                container_name = containers[0] if containers else "vllm"
                resources = self.get_pod_resources(pod, container=container_name)
                limits = resources.get("limits", {})

                # No real GPU resources on standalone either
                has_gpu = any(
                    "gpu" in k.lower() for k in limits
                    if k not in ("cpu", "memory", "ephemeral-storage")
                )
                report.add(CheckResult(
                    "standalone_no_gpu",
                    not has_gpu,
                    message=f"Standalone: {'no' if not has_gpu else 'found'} GPU resources",
                ))

                # Check standalone resources from config
                standalone_resources = _nested_get(config, "standalone", "resources") or {}
                for section in ("limits", "requests"):
                    for field in ("memory", "cpu"):
                        expected = _nested_get(standalone_resources, section, field)
                        if expected is not None:
                            report.add(self.assert_resource_matches(
                                resources, str(expected), f"{section}.{field}",
                            ))

        prefill_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role=prefill",
        )
        if prefill_pods:
            pod = prefill_pods[0]
            resources = self.get_pod_resources(pod)
            limits = resources.get("limits", {})

            has_gpu = any(
                "gpu" in k.lower() for k in limits
                if k not in ("cpu", "memory", "ephemeral-storage")
            )
            report.add(CheckResult(
                "prefill_no_gpu",
                not has_gpu,
                message=f"Prefill: {'no' if not has_gpu else 'found'} GPU resources -- simulated accelerator",
            ))
        elif _nested_get(config, "prefill", "enabled") is False:
            report.add(CheckResult(
                "prefill_disabled",
                len(prefill_pods) == 0,
                message=f"Prefill disabled -- {'no' if not prefill_pods else len(prefill_pods)} prefill pod(s)",
            ))

        return report
