"""Validator for the pd-disaggregation well-lit path."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class PdDisaggregationValidator(BaseSmoketest):
    """Validates prefill/decode disaggregation scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify both prefill and decode pod groups are deployed when disaggregation is enabled."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] pd-disaggregation config validation skipped",
            ))
            return report

        model_short = config.get("model_id_label", "") or _nested_get(config, "model", "shortName") or ""

        prefill_enabled = _nested_get(config, "prefill", "enabled")
        if prefill_enabled:
            prefill_pods = self.validate_role_pods(
                cmd, namespace, config, "prefill", model_short, report, logger=context.logger,
            )
        else:
            # Verify no prefill pods exist
            prefill_pods = self.get_pod_specs(
                cmd, namespace,
                f"llm-d.ai/model={model_short},llm-d.ai/role=prefill",
            )
            report.add(CheckResult(
                "prefill_disabled",
                len(prefill_pods) == 0,
                message=f"Prefill disabled -- {'no' if not prefill_pods else len(prefill_pods)} prefill pod(s)",
            ))

        decode_pods = self.validate_role_pods(
            cmd, namespace, config, "decode", model_short, report, logger=context.logger,
        )

        if decode_pods:
            # Shared memory volume -- only check if scenario defines it
            configured_volumes = _nested_get(config, "vllmCommon", "volumes") or []
            configured_vol_names = [v.get("name", "") for v in configured_volumes if isinstance(v, dict)]
            if "dshm" in configured_vol_names:
                volumes = self.get_pod_volumes(decode_pods[0])
                shm_size = _nested_get(config, "decode", "shm", "size")
                report.add(CheckResult(
                    "dshm_volume",
                    "dshm" in volumes,
                    expected=f"dshm ({shm_size})" if shm_size else "dshm",
                    message=f"Shared memory volume 'dshm' {'present' if 'dshm' in volumes else 'not found'}",
                ))

        return report
