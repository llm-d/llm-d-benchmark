"""Step 00 -- Validate system dependencies and print cluster summary banner."""

import os
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.deps import (
    check_system_dependencies,
    check_python_version,
    check_helm_version,
    check_helmfile_version,
    MIN_HELM_MAJOR,
    MIN_HELMFILE_VERSION,
)
from llmdbenchmark.utilities.cluster import print_phase_banner


class EnsureInfraStep(Step):
    """Validate system dependencies and print cluster summary banner."""

    def __init__(self):
        super().__init__(
            number=0,
            name="ensure_infra",
            description="Validate system dependencies and cluster connectivity",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        # No-Kubernetes: validate the container runtime + GPU + ports + token
        # instead of the helm/kubectl/cluster toolchain.
        if "nok8s" in (context.deployed_methods or []):
            return self._check_nok8s_infra(context)

        errors = []

        py_ok, py_version = check_python_version()
        if not py_ok:
            errors.append(f"Python >= 3.11 required, found {py_version}")

        dep_result = check_system_dependencies()
        if dep_result.has_missing_required:
            errors.append(
                f"Missing required tools: {', '.join(dep_result.missing_required)}"
            )

        if dep_result.missing_optional:
            if context.logger:
                for tool in dep_result.missing_optional:
                    context.logger.log_warning(f"Optional tool not found: {tool}")

        # Helm 4 toolchain guard. Standup deploys via helmfile; a Helm-3 host
        # or a pre-1.5 helmfile makes `helmfile template` panic with an
        # opaque "unknown flag: --client" error. Fail fast here with an
        # actionable message instead. Skipped on --dry-run (nothing deploys)
        # and only when the tool is actually present (a missing tool is
        # already reported above).
        if not context.dry_run:
            if "helm" in dep_result.available:
                helm_ok, helm_ver = check_helm_version()
                if not helm_ok:
                    errors.append(
                        f"Helm >= {MIN_HELM_MAJOR}.x required for standup "
                        f"(found {helm_ver}). Run ./install.sh to install the "
                        f"pinned Helm 4 toolchain."
                    )
            if "helmfile" in dep_result.available:
                hf_ok, hf_ver = check_helmfile_version()
                if not hf_ok:
                    min_hf = ".".join(str(p) for p in MIN_HELMFILE_VERSION)
                    errors.append(
                        f"helmfile >= {min_hf} required (Helm 4 compatible; "
                        f"found {hf_ver}). Older helmfile panics under Helm 4. "
                        f"Run ./install.sh to install the pinned helmfile."
                    )

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Infrastructure checks failed",
                errors=errors,
            )

        print_phase_banner(
            context,
            extra_fields={
                "Python": py_version,
            },
        )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"All checks passed. "
                f"Tools: {', '.join(dep_result.available)}. "
                f"Python: {py_version}. "
                f"Platform: {context.platform_type}"
            ),
            context={
                "python_version": py_version,
                "available_tools": dep_result.available,
                "missing_optional": dep_result.missing_optional,
                "is_openshift": context.is_openshift,
                "is_kind": context.is_kind,
                "is_minikube": context.is_minikube,
                "platform_type": context.platform_type,
                "cluster_name": context.cluster_name,
                "cluster_server": context.cluster_server,
            },
        )

    def _check_nok8s_infra(self, context: ExecutionContext) -> StepResult:
        """Preflight for the no-Kubernetes method: container runtime, GPU,
        ports, and HF token. Only a missing/broken runtime is fatal; the rest
        are loud warnings (vLLM surfaces GPU/token issues clearly at launch)."""
        runtime = context.container_runtime or "docker"
        plan_config = self._load_plan_config(context) or {}
        nok8s = plan_config.get("nok8s", {})
        accelerator = str(nok8s.get("vllm", {}).get("accelerator", "nvidia")).lower()
        hf_env = nok8s.get("hfTokenEnv", "HUGGING_FACE_HUB_TOKEN")
        ports = [
            nok8s.get("vllm", {}).get("hostPort", 8000),
            nok8s.get("envoy", {}).get("listenPort", 8081),
            nok8s.get("epp", {}).get("grpcPort", 9002),
            nok8s.get("epp", {}).get("grpcHealthPort", 9003),
            nok8s.get("epp", {}).get("metricsPort", 9090),
        ]

        if context.dry_run:
            context.logger.log_info(
                f"[dry-run] nok8s preflight: would verify '{runtime}' runtime, "
                f"'{accelerator}' accelerator, free ports {ports}, and ${hf_env}."
            )
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="nok8s preflight skipped (dry-run)",
            )

        cmd = context.require_cmd()
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Container runtime present and usable (fatal).
        if not cmd.execute(
            f"command -v {runtime}", check=False, force=True, silent=True
        ).success:
            errors.append(
                f"Container runtime '{runtime}' not found on PATH. Install docker "
                f"or podman (or set nok8s.runtime to the one you have)."
            )
        elif not cmd.execute(
            f"{runtime} info", check=False, force=True, silent=True
        ).success:
            errors.append(
                f"'{runtime}' is installed but not usable (daemon down or "
                f"permissions). Verify with '{runtime} info'."
            )

        # 1b. Host tools the nok8s path shells out to (fatal). The k8s
        #     toolchain check does not run for nok8s, so these would otherwise
        #     go unchecked: 'timeout' bounds the harness wait in step 07 and
        #     'curl' probes endpoint readiness in step 06.
        for tool in ("timeout", "curl"):
            if not cmd.execute(
                f"command -v {tool}", check=False, force=True, silent=True
            ).success:
                errors.append(
                    f"'{tool}' not found on PATH; the nok8s path needs it "
                    f"(install GNU coreutils and curl)."
                )

        # 2. Accelerator visible on the host (warning). Probe depends on the
        #    configured accelerator; cpu/spyre/custom are not probed here.
        probe = {
            "nvidia": "nvidia-smi -L",
            "amd": "rocm-smi --showid",
            "intel": "xpu-smi discovery",
            "gaudi": "hl-smi -L",
        }.get(accelerator)
        if probe:
            if not cmd.execute(probe, check=False, force=True, silent=True).success:
                tool = probe.split()[0]
                warnings.append(
                    f"'{tool}' found no {accelerator} accelerator; vLLM needs the "
                    f"device + driver present, the matching vLLM image, and the "
                    f"container toolkit configured for '{runtime}'."
                )
        else:
            context.logger.log_info(
                f"    accelerator='{accelerator}': skipping device probe "
                f"(cpu/spyre/custom -- ensure nok8s.vllm.deviceArgs + image match)."
            )

        # 2b. GPU capacity: replicas x tensorParallel must fit the host's GPUs.
        #     Only checkable for nvidia (nvidia-smi -L enumerates devices).
        vllm = nok8s.get("vllm", {})
        replicas = int(vllm.get("replicas", 1) or 1)
        tp = int(vllm.get("tensorParallel", 1) or 1)
        needed = replicas * tp
        if accelerator == "nvidia" and needed > 1:
            res_gpu = cmd.execute("nvidia-smi -L", check=False, force=True, silent=True)
            if res_gpu.success and res_gpu.stdout:
                count = sum(
                    1 for ln in res_gpu.stdout.splitlines() if ln.startswith("GPU ")
                )
                if count and needed > count:
                    warnings.append(
                        f"nok8s.vllm needs {needed} GPUs (replicas {replicas} x "
                        f"tensorParallel {tp}) but only {count} detected -- workers "
                        f"will contend for devices or fail to start."
                    )

        # 3. Hugging Face token (warning).
        if not os.environ.get(hf_env):
            warnings.append(
                f"${hf_env} is not set; gated Hugging Face models will fail to "
                f"download in the vLLM container."
            )

        # 4. Required host ports free (warning).
        res = cmd.execute("ss -ltn", check=False, force=True, silent=True)
        if res.success and res.stdout:
            busy = [p for p in ports if f":{p} " in res.stdout]
            if busy:
                warnings.append(
                    f"Host ports already in use: {busy}. Free them, run "
                    f"'teardown', or change the nok8s ports."
                )

        for w in warnings:
            context.logger.log_warning(f"    {w}")
        if errors:
            for e in errors:
                context.logger.log_error(f"    {e}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="nok8s infrastructure checks failed",
                errors=errors,
            )

        context.logger.log_info(f"nok8s preflight passed (runtime={runtime}).")
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"nok8s infrastructure ready (runtime={runtime})",
        )
