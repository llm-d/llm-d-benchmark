"""Step 06 -- Deploy the llm-d stack as local containers (no Kubernetes).

Launches vLLM worker(s) + EPP (router) + Envoy as docker/podman containers on
the host, driven by the rendered ``34_nok8s-containers.yaml`` launch spec and
the ``31/32/33_nok8s-*`` config files.  No cluster is involved; the EPP uses
the file-discovery plugin (reads endpoints.yaml).
"""

import os
import time
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class NoK8sDeployStep(Step):
    """Deploy the llm-d routing stack as local containers, no Kubernetes."""

    def __init__(self):
        super().__init__(
            number=6,
            name="nok8s_deploy",
            description="Deploy vLLM + EPP + Envoy as local containers (no k8s)",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "nok8s" not in context.deployed_methods

    def execute(  # pylint: disable=too-many-branches,too-many-locals,too-many-return-statements
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return self._fail(None, "No stack path provided for per-stack step")

        cmd = context.require_cmd()

        spec_yaml = self._find_yaml(stack_path, "34_nok8s-containers")
        if not spec_yaml or not self._has_yaml_content(spec_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=True,
                message="No nok8s container spec found, skipping",
                stack_name=stack_path.name,
            )
        spec = yaml.safe_load(spec_yaml.read_text(encoding="utf-8")) or {}

        runtime = spec.get("runtime", context.container_runtime or "docker")
        workspace = Path(os.path.expanduser(spec["workspaceHostDir"]))
        hf_cache = os.path.expanduser(spec.get("hfCacheDir", "~/.cache/huggingface"))
        hf_token_env = spec.get("hfTokenEnv", "HUGGING_FACE_HUB_TOKEN")
        model = spec["model"]
        endpoint = spec["endpoint"]
        containers = spec.get("containers", [])

        # Record the endpoint up front so downstream (even dry-run) can use it.
        context.deployed_endpoints[stack_path.name] = endpoint

        if hf_token_env not in os.environ and not context.dry_run:
            context.logger.log_warning(
                f"{hf_token_env} not set in the environment; gated Hugging Face "
                f"models will fail to download in the vLLM container."
            )

        # Stage the EPP/Envoy config files to the host workspace dir.
        epp_dir = workspace / "epp"
        if not context.dry_run:
            self._stage_configs(stack_path, workspace, epp_dir)
        else:
            context.logger.log_info(
                f"[dry-run] would stage nok8s configs under {workspace}"
            )

        errors: list[str] = []
        launched: list[str] = []
        for c in containers:
            name = c["name"]
            # Idempotency: remove any prior container with this name.
            cmd.execute(f"{runtime} rm -f {name}", check=False)
            run_cmd = self._build_run_command(
                c, runtime, workspace, epp_dir, hf_cache, hf_token_env, model
            )
            result = cmd.execute(run_cmd, check=False)
            if not result.success and not context.dry_run:
                errors.append(f"Failed to start container {name}: {result.stderr}")
                self._dump_logs(cmd, runtime, name, context)
            else:
                launched.append(name)

        if errors:
            return self._fail(stack_path, "; ".join(errors), errors)

        # Readiness: each vLLM worker, then Envoy.
        if not context.dry_run:
            ready_err = self._wait_ready(cmd, runtime, spec, context)
            if ready_err:
                self._dump_logs(cmd, runtime, "envoy", context)
                return self._fail(stack_path, ready_err, [ready_err])

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"nok8s stack up: {', '.join(launched)} -> {endpoint}",
            stack_name=stack_path.name,
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _stage_configs(self, stack_path: Path, workspace: Path, epp_dir: Path) -> None:
        """Copy rendered EPP/Envoy config files to the host workspace dir."""
        epp_dir.mkdir(parents=True, exist_ok=True)
        mapping = {
            "31_nok8s-epp-config": epp_dir / "config.yaml",
            "32_nok8s-epp-endpoints": epp_dir / "endpoints.yaml",
            "33_nok8s-envoy": workspace / "envoy.yaml",
        }
        for prefix, dest in mapping.items():
            src = self._find_yaml(stack_path, prefix)
            if src and self._has_yaml_content(src):
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    def _build_run_command(  # pylint: disable=too-many-arguments
        self,
        c: dict,
        runtime: str,
        workspace: Path,
        epp_dir: Path,
        hf_cache: str,
        hf_token_env: str,
        model: str,
    ) -> str:
        """Build the docker/podman run command for one container from its spec."""
        kind = c["kind"]
        image = c["image"]
        if kind == "vllm":
            device = self._device_args(runtime, c)
            pin = self._pin_env(c)
            extra = " ".join(c.get("extraArgs") or [])
            return (
                f"{runtime} run -d --name {c['name']}"
                + (f" {device}" if device else "")
                + (f" {pin}" if pin else "")
                + f" --shm-size={c.get('shmSize', '20g')} "
                f"-p {c['hostPort']}:{c.get('containerPort', 8000)} "
                f"-e {hf_token_env} "
                f"-v {hf_cache}:/root/.cache/huggingface "
                f"--entrypoint vllm {image} "
                f"serve {model} "
                f"--disable-access-log-for-endpoints=/health,/metrics,/v1/models "
                f"--tensor-parallel-size={c.get('tensorParallel', 1)}"
                + (f" {extra}" if extra else "")
            )
        if kind == "epp":
            mount_dir = c.get("configMountDir", "/etc/epp")
            return (
                f"{runtime} run -d --name {c['name']} --network host "
                f"-v {epp_dir}:{mount_dir}:ro {image} "
                f"--config-file={mount_dir}/config.yaml "
                f"--pool-name={c.get('poolName', 'file-discovery')} "
                f"--pool-namespace={c.get('poolNamespace', 'default')} "
                f"--grpc-port={c['grpcPort']} "
                f"--grpc-health-port={c['grpcHealthPort']} "
                f"--metrics-port={c['metricsPort']} "
                f"--secure-serving=false --v=2"
            )
        if kind == "envoy":
            mount_path = c.get("configMountPath", "/etc/envoy/envoy.yaml")
            return (
                f"{runtime} run -d --name {c['name']} --network host "
                f"-v {workspace / 'envoy.yaml'}:{mount_path}:ro {image} "
                f"--service-node envoy-proxy --log-level warn --concurrency 8 "
                f"--drain-strategy immediate --drain-time-s 60 -c {mount_path}"
            )
        raise ValueError(f"Unknown nok8s container kind: {kind}")

    # Per-accelerator env var that pins a process to a subset of devices.
    _VISIBLE_DEVICE_ENV = {
        "nvidia": "CUDA_VISIBLE_DEVICES",
        "amd": "HIP_VISIBLE_DEVICES",
        "intel": "ZE_AFFINITY_MASK",
    }

    @classmethod
    def _pin_env(cls, c: dict) -> str:
        """Per-replica GPU pinning flag (``-e <VISIBLE_DEVICES>=<slice>``).

        With ``replicas > 1`` each replica is pinned to its own contiguous slice
        of ``tensorParallel`` device indices (replica *i* -> devices
        ``i*TP .. i*TP+TP-1``) so workers don't contend for the same GPUs.
        Returns "" for a single replica (keeps the current --gpus all behaviour),
        when ``deviceArgs`` is set (caller controls devices), or for accelerators
        without an index-based visible-devices env (gaudi/cpu/spyre).
        """
        replicas = int(c.get("replicas", 1) or 1)
        if replicas <= 1 or c.get("deviceArgs"):
            return ""
        accel = str(c.get("accelerator") or "nvidia").lower()
        var = cls._VISIBLE_DEVICE_ENV.get(accel)
        if not var:
            return ""
        tp = int(c.get("tensorParallel", 1) or 1)
        idx = int(c.get("replicaIndex", 0) or 0)
        devices = ",".join(str(d) for d in range(idx * tp, idx * tp + tp))
        return f"-e {var}={devices}"

    @staticmethod
    def _device_args(runtime: str, c: dict) -> str:
        """Runtime flags to expose the accelerator to the vLLM container.

        ``deviceArgs`` (if set) is a raw override -- use it for anything not
        covered by the presets below (e.g. IBM Spyre/AIU). Otherwise the
        ``accelerator`` field selects a preset. Only NVIDIA is validated
        end-to-end; the others follow each backend's documented device flags.
        """
        raw = c.get("deviceArgs")
        if raw:
            return " ".join(raw)
        accel = str(c.get("accelerator") or "nvidia").lower()
        gpus = c.get("gpus", "all")
        if accel == "nvidia":
            # docker: --gpus; podman: CDI device.
            return (
                f"--device nvidia.com/gpu={gpus}"
                if runtime == "podman"
                else f"--gpus {gpus}"
            )
        if accel == "amd":
            return "--device /dev/kfd --device /dev/dri --group-add video"
        if accel == "intel":
            return "--device /dev/dri"
        if accel == "gaudi":
            return "--runtime=habana -e HABANA_VISIBLE_DEVICES=all"
        if accel in ("cpu", "spyre"):
            # cpu: no device; spyre/AIU: supply nok8s.vllm.deviceArgs.
            return ""
        # Unknown accelerator: fall back to NVIDIA behaviour.
        return f"--gpus {gpus}"

    def _wait_ready(
        self, cmd: CommandExecutor, runtime: str, spec: dict, context: ExecutionContext
    ) -> str | None:
        """Poll vLLM workers then Envoy for /v1/models. Returns error string or None."""
        readiness = spec.get("readiness", {})
        ports = list(readiness.get("vllmPorts", [])) + [readiness.get("envoyPort")]
        timeout = context.nok8s_deploy_timeout
        for port in ports:
            if port is None:
                continue
            url = f"http://localhost:{port}/v1/models"
            deadline = time.time() + timeout
            ok = False
            while time.time() < deadline:
                result = cmd.execute(f"curl -fsS {url}", check=False, force=True)
                if result.success:
                    ok = True
                    break
                time.sleep(10)
            if not ok:
                return f"Timed out waiting for {url} after {timeout}s"
            context.logger.log_info(f"nok8s endpoint ready: {url}")
        return None

    def _dump_logs(
        self, cmd: CommandExecutor, runtime: str, name: str, context: ExecutionContext
    ) -> None:
        """Best-effort capture of a container's logs into the setup logs dir."""
        result = cmd.execute(
            f"{runtime} logs {name} --tail 100", check=False, force=True
        )
        try:
            log_path = context.setup_logs_dir() / f"nok8s-{name}.log"
            log_path.write_text(
                (result.stdout or "") + (result.stderr or ""), encoding="utf-8"
            )
        except OSError:
            pass

    def _fail(
        self, stack_path: Path | None, message: str, errors: list[str] | None = None
    ) -> StepResult:
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=message,
            errors=errors or [message],
            stack_name=stack_path.name if stack_path else None,
        )
