"""Validator for the tiered-prefix-cache well-lit path."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class TieredPrefixCacheValidator(BaseSmoketest):
    """Validates tiered prefix cache scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify decode pods carry the expected additional vLLM flags and EPP pod is running."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] tiered-prefix-cache config validation skipped",
            ))
            return report

        model_short = _nested_get(config, "model", "shortName") or ""

        # ── No prefill pods (decode-only) ─────────────────────────────
        prefill_pods = self.get_pod_specs(
            cmd, namespace,
            f"llm-d.ai/model={model_short},llm-d.ai/role=prefill",
        )
        report.add(CheckResult(
            "no_prefill_pods",
            len(prefill_pods) == 0,
            message=f"{'No' if not prefill_pods else len(prefill_pods)} prefill pod(s) — decode-only",
        ))

        # ── Decode pods (comprehensive) ───────────────────────────────
        decode_pods = self.validate_role_pods(
            cmd, namespace, config, "decode", model_short, report, logger=context.logger,
        )

        if decode_pods:
            pod = decode_pods[0]
            args = self.get_pod_args(pod)

            # Scenario-specific: additional flags from config
            additional_flags = _nested_get(config, "decode", "vllm", "additionalFlags") or []
            for flag in additional_flags:
                if isinstance(flag, str) and flag.startswith("--"):
                    # Split flag into name and value if applicable
                    parts = flag.split(None, 1)
                    flag_name = parts[0]
                    flag_value = parts[1] if len(parts) > 1 else None
                    report.add(self.assert_arg_contains(args, flag_name, flag_value))

        # ── EPP pod running ───────────────────────────────────────────
        epp_pods = self.get_pod_specs(
            cmd, namespace,
            f"inferencepool={model_short}-gaie-epp",
        )
        report.add(CheckResult(
            "epp_pod_running",
            len(epp_pods) > 0,
            message=f"EPP pod {'running' if epp_pods else 'not found'}",
        ))

        # ── Shared memory volume ──────────────────────────────────────
        if decode_pods:
            volumes = self.get_pod_volumes(decode_pods[0])
            report.add(CheckResult(
                "dshm_volume",
                "dshm" in volumes,
                message=f"Shared memory volume 'dshm' {'present' if 'dshm' in volumes else 'not found'}",
            ))

        return report
