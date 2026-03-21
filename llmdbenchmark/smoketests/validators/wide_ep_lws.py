"""Validator for the wide-ep-lws well-lit path."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class WideEpLwsValidator(BaseSmoketest):
    """Validates wide expert-parallel with LeaderWorkerSet scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify expert-parallel flags, LWS env vars, multi-NIC annotations, and RDMA resources."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] wide-ep-lws config validation skipped",
            ))
            return report

        model_short = _nested_get(config, "model", "shortName") or ""

        decode_pods = self.validate_role_pods(
            cmd, namespace, config, "decode", model_short, report, logger=context.logger,
        )

        if decode_pods:
            pod = decode_pods[0]
            args = self.get_pod_args(pod)
            env = self.get_pod_env(pod)
            resources = self.get_pod_resources(pod)

            # Scenario-specific: expert parallelism flags
            report.add(self.assert_arg_contains(args, "--enable-expert-parallel"))
            report.add(self.assert_arg_contains(args, "--data-parallel-size-local"))

            # Scenario-specific: LWS env vars (injected by LeaderWorkerSet controller)
            if "LWS_GROUP_SIZE" in env:
                report.add(CheckResult(
                    "lws_group_size", True,
                    message=f"LWS_GROUP_SIZE={env['LWS_GROUP_SIZE']}",
                ))
            else:
                report.add(CheckResult(
                    "lws_group_size", False,
                    message="LWS_GROUP_SIZE env var not set — LWS may not be active",
                ))

            if "LWS_LEADER_ADDRESS" in env:
                report.add(CheckResult(
                    "lws_leader_address", True,
                    message="LWS_LEADER_ADDRESS set",
                ))

            # Scenario-specific: Multi-NIC annotation
            annotations = self.get_pod_annotations(pod)
            expected_annotation = _nested_get(config, "annotations", "decode", "pod") or {}
            if expected_annotation:
                for ann_key, ann_val in expected_annotation.items():
                    actual_val = annotations.get(ann_key)
                    report.add(CheckResult(
                        f"annotation_{ann_key.split('/')[-1]}",
                        actual_val == ann_val,
                        expected=str(ann_val),
                        actual=str(actual_val),
                        message=f"Annotation {ann_key}: {'matches' if actual_val == ann_val else 'mismatch'}",
                    ))
            else:
                # Fall back to checking for any CNI annotation
                has_multi_nic = any("cni.cncf.io" in k for k in annotations)
                report.add(CheckResult(
                    "multi_nic_annotation",
                    has_multi_nic,
                    message=f"Multi-NIC annotation {'present' if has_multi_nic else 'not found'}",
                ))

            # Scenario-specific: RDMA network resource
            limits = resources.get("limits", {})
            has_rdma = any("rdma" in k or "roce" in k for k in limits)
            network_resource = _nested_get(config, "vllmCommon", "networkResource")
            if network_resource and network_resource != "auto":
                report.add(CheckResult(
                    "rdma_network_resource",
                    has_rdma,
                    expected=str(network_resource),
                    message=f"RDMA network resource {'present' if has_rdma else 'not found'} in pod limits",
                ))

            # Scenario-specific: ephemeral storage
            expected_eph = _nested_get(config, "decode", "resources", "limits", "ephemeral-storage")
            if expected_eph:
                report.add(self.assert_resource_matches(
                    resources, str(expected_eph), "limits.ephemeral-storage",
                ))

            # Scenario-specific: mount model volume override
            mount_model = _nested_get(config, "decode", "mountModelVolume")
            if mount_model is False:
                volumes = self.get_pod_volumes(pod)
                mounts = self._get_container_volume_mounts(pod)
                # When mountModelVolume is false, model-cache mount should be absent
                report.add(CheckResult(
                    "no_model_volume_mount",
                    "model-cache" not in mounts and "model-pvc" not in mounts,
                    message=f"Model volume mount correctly {'absent' if 'model-cache' not in mounts and 'model-pvc' not in mounts else 'present (unexpected)'}",
                ))

        prefill_enabled = _nested_get(config, "prefill", "enabled")
        if prefill_enabled:
            prefill_pods = self.validate_role_pods(
                cmd, namespace, config, "prefill", model_short, report, logger=context.logger,
            )

            if prefill_pods:
                prefill_args = self.get_pod_args(prefill_pods[0])
                report.add(self.assert_arg_contains(prefill_args, "--enable-expert-parallel"))

        total_pods = len(decode_pods) + (len(prefill_pods) if prefill_enabled and 'prefill_pods' in dir() else 0)
        report.add(CheckResult(
            "pods_found",
            total_pods > 0,
            message=f"Total pods found: {total_pods}",
        ))

        return report
