"""Validator for the CPU example scenario."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class CpuValidator(BaseSmoketest):
    """Validates CPU-only deployment scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify CPU-only pods have no GPU resources, correct images, and expected volumes."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] cpu config validation skipped",
            ))
            return report

        model_short = config.get("model_id_label", "") or _nested_get(config, "model", "shortName") or ""

        prefill_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role=prefill",
        )
        report.add(CheckResult(
            "no_prefill_pods",
            len(prefill_pods) == 0,
            message=f"{'No' if not prefill_pods else len(prefill_pods)} prefill pod(s) -- no prefill expected",
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
            # Run comprehensive decode checks from base
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

        # No GPU resources
        has_gpu = any(
            "gpu" in k.lower() for k in limits
            if k not in ("cpu", "memory", "ephemeral-storage")
        )
        report.add(CheckResult(
            "no_gpu_resources",
            not has_gpu,
            message=f"{'No' if not has_gpu else 'Found'} GPU resources -- CPU-only deployment",
        ))

        # Correct vLLM image
        for c in containers:
            if c.get("name") == container_name:
                image = c.get("image", "")
                expected_repo = _nested_get(config, "images", "vllm", "repository") or ""
                if expected_repo:
                    report.add(CheckResult(
                        "cpu_vllm_image",
                        expected_repo in image,
                        expected=expected_repo,
                        actual=image,
                        message=f"vLLM image: {image}",
                    ))
                break

        # Volumes
        volumes = self.get_pod_volumes(serving_pod)
        report.add(CheckResult(
            "kubeconfig_volume",
            "k8s-llmdbench-context" in volumes,
            message=f"Kubeconfig secret volume {'present' if 'k8s-llmdbench-context' in volumes else 'not found'}",
        ))
        report.add(CheckResult(
            "preprocesses_volume",
            "preprocesses" in volumes,
            message=f"Preprocesses configMap volume {'present' if 'preprocesses' in volumes else 'not found'}",
        ))
        # Shared memory volume -- only check if scenario defines it
        configured_volumes = _nested_get(config, "vllmCommon", "volumes") or []
        configured_vol_names = [v.get("name", "") for v in configured_volumes if isinstance(v, dict)]
        if "dshm" in configured_vol_names:
            report.add(CheckResult(
                "dshm_volume",
                "dshm" in volumes,
                message=f"Shared memory volume 'dshm' {'present' if 'dshm' in volumes else 'not found'}",
            ))

        return report
