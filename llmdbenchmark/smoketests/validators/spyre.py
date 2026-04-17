"""Validator for the IBM Spyre accelerator scenario."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class SpyreValidator(BaseSmoketest):
    """Validates IBM Spyre accelerator deployment scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify Spyre accelerator resource, env vars, precompiled model volume, and vLLM image."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] spyre config validation skipped",
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
            self.validate_role_pods(
                cmd, namespace, config, "decode", model_short, report, logger=context.logger,
            )
            serving_pod = decode_pods[0]
            container_name = "vllm"
        elif standalone_pods:
            report.add(CheckResult(
                "deploy_method", True,
                message=f"Deployed via standalone -- {len(standalone_pods)} standalone pod(s)",
            ))
            serving_pod = standalone_pods[0]
            # Standalone container name varies
            containers = self.get_pod_containers(serving_pod)
            container_name = containers[0] if containers else "vllm"
        else:
            report.add(CheckResult(
                "deploy_method", False,
                message="No decode or standalone pods found",
            ))
            return report

        resources = self.get_pod_resources(serving_pod, container=container_name)
        limits = resources.get("limits", {})

        # Spyre accelerator resource
        accel_resource = _nested_get(config, "decode", "accelerator", "resource") or \
            _nested_get(config, "accelerator", "resource") or "ibm.com/spyre_vf"
        has_spyre = accel_resource in limits
        report.add(CheckResult(
            "spyre_accelerator",
            has_spyre,
            expected=accel_resource,
            message=f"Spyre accelerator '{accel_resource}' {'present' if has_spyre else 'not found'} in pod limits",
        ))

        # Spyre-specific environment variables
        env_vars = self._get_container_env(serving_pod, container=container_name)
        pod_name = serving_pod.get("metadata", {}).get("name", "unknown")

        # Determine expected FLEX_DEVICE from scenario config (extraEnvVars)
        expected_flex_device = "VF"
        for section in ("decode", "standalone"):
            env_list = _nested_get(config, section, "extraEnvVars") or []
            for ev in env_list:
                if isinstance(ev, dict) and ev.get("name") == "FLEX_DEVICE":
                    expected_flex_device = ev.get("value", "VF")
                    break

        spyre_env_checks = [
            ("FLEX_COMPUTE", "SENTIENT"),
            ("FLEX_DEVICE", expected_flex_device),
            ("VLLM_SPYRE_DYNAMO_BACKEND", "sendnn"),
            ("VLLM_SPYRE_USE_CB", "1"),
            ("TORCH_SENDNN_CACHE_ENABLE", "1"),
        ]

        for env_name, expected_val in spyre_env_checks:
            actual = env_vars.get(env_name)
            report.add(CheckResult(
                f"env_{env_name}",
                actual == expected_val,
                expected=expected_val,
                actual=str(actual),
                message=f"{env_name}={actual} in pod/{pod_name} container/{container_name} env"
                if actual == expected_val
                else f"{env_name} expected={expected_val} actual={actual} in pod/{pod_name} container/{container_name} env",
            ))

        # Volumes
        volumes = self.get_pod_volumes(serving_pod)
        report.add(CheckResult(
            "spyre_precompiled_volume",
            "spyre-precompiled-model" in volumes,
            message=f"Spyre precompiled model volume {'present' if 'spyre-precompiled-model' in volumes else 'not found'}",
        ))
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

        # Correct vLLM image
        containers_list = serving_pod.get("spec", {}).get("containers", [])
        for c in containers_list:
            if c.get("name") == container_name:
                image = c.get("image", "")
                expected_repo = _nested_get(config, "images", "vllm", "repository") or ""
                if expected_repo:
                    report.add(CheckResult(
                        "spyre_vllm_image",
                        expected_repo in image,
                        expected=expected_repo,
                        actual=image,
                        message=f"Spyre vLLM image: {image}",
                    ))
                break

        return report

    @staticmethod
    def _get_container_env(pod_spec: dict, container: str = "vllm") -> dict:
        """Extract env vars as a dict from the named container."""
        for c in pod_spec.get("spec", {}).get("containers", []):
            if c.get("name") == container:
                return {
                    e["name"]: e.get("value", "")
                    for e in c.get("env", [])
                    if "name" in e
                }
        return {}
