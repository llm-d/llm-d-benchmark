"""Validator for the precise-prefix-cache-aware well-lit path."""

from pathlib import Path

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.smoketests.base import BaseSmoketest, _load_config, _nested_get
from llmdbenchmark.smoketests.report import CheckResult, SmoketestReport


class PrecisePrefixCacheAwareValidator(BaseSmoketest):
    """Validates precise prefix cache aware routing scenario."""

    def run_config_validation(
        self, context: ExecutionContext, stack_path: Path,
    ) -> SmoketestReport:
        """Verify sha256_cbor hash algo, direct vLLM port (no proxy), and EPP pod presence."""
        report = SmoketestReport()
        cmd = context.require_cmd()
        namespace = context.require_namespace()
        config = _load_config(stack_path)

        if context.dry_run:
            report.add(CheckResult(
                "config_validation", True,
                message="[DRY RUN] precise-prefix-cache-aware config validation skipped",
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
            expected="0", actual=str(len(prefill_pods)),
            message=f"{'No' if not prefill_pods else len(prefill_pods)} prefill pod(s) — decode-only scenario",
        ))

        # ── Decode pods (comprehensive) ───────────────────────────────
        decode_pods = self.validate_role_pods(
            cmd, namespace, config, "decode", model_short, report, logger=context.logger,
        )

        if decode_pods:
            pod = decode_pods[0]
            args = self.get_pod_args(pod)

            # Scenario-specific: prefix caching hash algo
            report.add(self.assert_arg_contains(args, "--prefix-caching-hash-algo", "sha256_cbor"))

            # Scenario-specific: vLLM port should match inference port (no proxy)
            vllm_port_in_args = None
            if "--port" in args:
                parts = args.split()
                for i, p in enumerate(parts):
                    if p == "--port" and i + 1 < len(parts):
                        vllm_port_in_args = parts[i + 1].strip("\\").strip()
                        break

            if vllm_port_in_args:
                expected_port = _nested_get(config, "vllmCommon", "inferencePort")
                if expected_port is not None:
                    report.add(CheckResult(
                        "vllm_port",
                        vllm_port_in_args in (str(expected_port), "$VLLM_INFERENCE_PORT"),
                        expected=str(expected_port),
                        actual=vllm_port_in_args,
                        message=f"vLLM port is {vllm_port_in_args} (expected {expected_port} — no proxy)",
                    ))

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
