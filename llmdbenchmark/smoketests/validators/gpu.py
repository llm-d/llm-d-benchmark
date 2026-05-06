"""Validator for the GPU example scenario."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class GpuValidator(BaseSmoketest):
    """Validates standard GPU deployment scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify GPU pods have the expected accelerator resource and shared memory volume."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] gpu config validation skipped",
            ))
            return report

        model_short = config.get("model_id_label", "") or _nested_get(config, "model", "shortName") or ""

        prefill_enabled = _nested_get(config, "prefill", "enabled")
        prefill_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role=prefill",
        )
        if prefill_enabled and prefill_pods:
            self.validate_role_pods(
                cmd, namespace, config, "prefill", model_short, report, logger=context.logger,
            )
        elif not prefill_enabled:
            report.add(CheckResult(
                "no_prefill_pods",
                len(prefill_pods) == 0,
                message=f"{'No' if not prefill_pods else len(prefill_pods)} prefill pod(s) -- prefill disabled",
            ))

        decode_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role=decode",
        )

        standalone_role = _nested_get(config, "standalone", "role") or "standalone"
        standalone_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role={standalone_role}",
        )

        if decode_pods:
            report.add(CheckResult(
                "deploy_method", True,
                message=f"Deployed via modelservice -- {len(decode_pods)} decode pod(s)",
            ))
            self.validate_role_pods(
                cmd, namespace, config, "decode", model_short, report, logger=context.logger,
            )
            serving_pod = decode_pods[0]
        elif standalone_pods:
            report.add(CheckResult(
                "deploy_method", True,
                message=f"Deployed via standalone -- {len(standalone_pods)} standalone pod(s)",
            ))
            serving_pod = standalone_pods[0]
        else:
            report.add(CheckResult(
                "deploy_method", False,
                message="No decode or standalone pods found",
            ))
            return report

        # Find the correct container name
        containers = serving_pod.get("spec", {}).get("containers", [])
        container_name = None
        for c in containers:
            if "vllm" in c.get("name", ""):
                container_name = c.get("name")
                break
        if not container_name and containers:
            container_name = containers[0].get("name", "vllm")

        resources = self.get_pod_resources(serving_pod, container=container_name or "vllm")
        limits = resources.get("limits", {})

        # GPU resources should be present
        accel_resource = _nested_get(config, "accelerator", "resource") or "nvidia.com/gpu"
        has_gpu = accel_resource in limits
        report.add(CheckResult(
            "gpu_resources",
            has_gpu,
            expected=accel_resource,
            message=f"GPU resource '{accel_resource}' {'present' if has_gpu else 'not found'} in pod limits",
        ))

        # Shared memory volume -- only check if scenario defines it
        configured_volumes = _nested_get(config, "vllmCommon", "volumes") or []
        configured_vol_names = [v.get("name", "") for v in configured_volumes if isinstance(v, dict)]
        if "dshm" in configured_vol_names:
            volumes = self.get_pod_volumes(serving_pod)
            report.add(CheckResult(
                "dshm_volume",
                "dshm" in volumes,
                message=f"Shared memory volume 'dshm' {'present' if 'dshm' in volumes else 'not found'}",
            ))

        return report
