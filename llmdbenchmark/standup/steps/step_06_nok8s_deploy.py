"""Step 06 -- Deploy the llm-d stack as containers (no Kubernetes).

Launches vLLM worker(s) + EPP (router) + Envoy as docker/podman containers,
driven by the rendered ``34_nok8s-containers.yaml`` launch spec and the
``31/32/33_nok8s-*`` config files.  No cluster is involved; the EPP uses the
file-discovery plugin (reads endpoints.yaml).

The containers run wherever ``nok8s.connection`` points -- the local host by
default, or a remote node over the runtime's SSH transport.  Remote is not just
"add ``-H ssh://``" to each ``run``: three things in this step resolve on the
machine that holds the files, not the one holding the client, and each is
handled explicitly below.

* **Bind-mount sources.** ``_stage_configs`` writes the EPP/Envoy configs, then
  every container mounts them by path. A path is resolved by the *daemon*, so
  for a remote host the staged files are pushed there first (docker would
  otherwise mount an empty directory and the EPP would come up with no
  endpoints; podman fails outright).
* **``~`` in paths.** ``workspaceHostDir`` and ``hfCacheDir`` are the remote
  user's home, so the home directory is read off the daemon host instead of
  expanding the client's ``$HOME``.
* **Readiness.** ``curl http://localhost:<port>`` from the client would probe
  the client. The probe is executed on the daemon host, so what it proves is
  that the node is serving -- which is also why the vLLM ports need no
  client-side reachability.
"""

import os
import shlex
import time
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.utilities.container_host import (
    ContainerHost,
    ContainerHostError,
    expand_remote_path,
)


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
        try:
            host = self._container_host(spec, runtime, context)
        except ContainerHostError as exc:
            return self._fail(stack_path, str(exc))

        hf_token_env = spec.get("hfTokenEnv", "HUGGING_FACE_HUB_TOKEN")
        model = spec["model"]
        endpoint = spec["endpoint"]
        containers = spec.get("containers", [])

        # ``~`` in these paths belongs to the user that owns the daemon, which
        # for a remote host is not the user running llmdbenchmark. Resolve it
        # against that host's home so the bind-mount source and the staged
        # files agree on one absolute path.
        home = self._daemon_home(cmd, host, context)
        workspace = Path(expand_remote_path(spec["workspaceHostDir"], home))
        hf_cache = expand_remote_path(
            spec.get("hfCacheDir", "~/.cache/huggingface"), home
        )

        # Record the endpoint up front so downstream (even dry-run) can use it.
        # This is the in-host URL: the harness runs on the daemon host with
        # --network host, so keeping it there measures the stack and not the
        # SSH link. The client-side URL is `clientEndpoint`.
        context.deployed_endpoints[stack_path.name] = endpoint
        if host.is_remote:
            context.logger.log_info(
                f"nok8s target: {host.describe()} "
                f"(client endpoint {spec.get('clientEndpoint') or endpoint})"
            )

        if hf_token_env not in os.environ and not context.dry_run:
            context.logger.log_warning(
                f"{hf_token_env} not set in the environment; gated Hugging Face "
                f"models will fail to download in the vLLM container."
            )

        # Stage the EPP/Envoy config files where the daemon will mount them.
        epp_dir = workspace / "epp"
        if not context.dry_run:
            stage_err = self._stage_configs(
                stack_path, workspace, epp_dir, cmd, host, context
            )
            if stage_err:
                return self._fail(stack_path, stage_err, [stage_err])
        else:
            context.logger.log_info(
                f"[dry-run] would stage nok8s configs under {workspace}"
                + (f" on {host.destination}" if host.is_remote else "")
            )

        launched: list[str] = []
        for c in containers:
            name = c["name"]
            # Idempotency: remove any prior container with this name.
            cmd.execute(host.runtime_cmd("rm", "-f", name), check=False)
            run_cmd, run_stdin = self._build_run_command(
                c, runtime, workspace, epp_dir, hf_cache, hf_token_env, model, host
            )
            result = cmd.execute(run_cmd, check=False, stdin=run_stdin)
            if not result.success and not context.dry_run:
                # The stack is unusable without every container, so stop at the
                # first failure instead of launching the rest, and remove what
                # is already up so it does not hold host ports (epp/envoy use
                # --network host) against the next standup.
                err = f"Failed to start container {name}: {result.stderr}"
                self._dump_logs(cmd, host, name, context)
                self._rollback(cmd, host, launched, context)
                return self._fail(stack_path, err, [err])
            launched.append(name)

        # Readiness: each vLLM worker, then Envoy.
        if not context.dry_run:
            ready_err = self._wait_ready(cmd, host, spec, context)
            if ready_err:
                envoy = next(
                    (c["name"] for c in containers if c.get("kind") == "envoy"), None
                )
                if envoy:
                    self._dump_logs(cmd, host, envoy, context)
                self._rollback(cmd, host, launched, context)
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
    @staticmethod
    def _container_host(
        spec: dict, runtime: str, context: ExecutionContext
    ) -> ContainerHost:
        """Resolve the connection target from the rendered spec.

        The render already rejected a malformed value, but the spec is a file on
        disk that can be hand-edited between plan and standup, so it is parsed
        (and can still fail) here too.
        """
        return ContainerHost.parse(
            spec.get("connection") or context.container_connection,
            runtime=runtime,
            identity=spec.get("sshIdentity") or "",
            ssh_args=spec.get("sshArgs") or None,
            transport=spec.get("transport") or "",
        )

    def _daemon_home(
        self, cmd: CommandExecutor, host: ContainerHost, context: ExecutionContext
    ) -> str:
        """``$HOME`` of the user owning the daemon, for expanding ``~`` paths.

        Local hosts keep using the client's ``$HOME`` (unchanged behaviour).
        For a remote host the value is read over SSH; if that fails the ``~`` is
        left for the remote shell to expand, which still works for the staging
        ``mkdir`` even though a bind-mount source cannot rely on it -- so the
        failure is logged rather than swallowed.

        Skipped under ``--dry-run``, which must not require the node to be
        reachable: the paths are then logged with their ``~`` intact.
        """
        if not host.is_remote:
            return os.path.expanduser("~")
        if context.dry_run:
            return ""
        result = cmd.execute(host.shell("printenv HOME"), check=False, force=True)
        home = (result.stdout or "").strip()
        if not home:
            context.logger.log_warning(
                f"Could not read $HOME on {host.destination} "
                f"({(result.stderr or '').strip()[:200]}); paths containing '~' "
                f"may not resolve. Use absolute paths for "
                f"nok8s.workspaceHostDir and nok8s.vllm.hfCacheDir."
            )
        return home

    def _stage_configs(  # pylint: disable=too-many-arguments
        self,
        stack_path: Path,
        workspace: Path,
        epp_dir: Path,
        cmd: CommandExecutor,
        host: ContainerHost,
        context: ExecutionContext,
    ) -> str | None:
        """Put the rendered EPP/Envoy configs where the daemon will mount them.

        Returns an error string on failure, or None. A remote staging failure is
        fatal rather than a warning: docker happily bind-mounts a path that does
        not exist on the daemon host as an empty directory, so the EPP would
        start, find no endpoints file, and route nothing -- a much harder failure
        to read than stopping here.
        """
        mapping = {
            "31_nok8s-epp-config": ("epp", "config.yaml"),
            "32_nok8s-epp-endpoints": ("epp", "endpoints.yaml"),
            "33_nok8s-envoy": ("", "envoy.yaml"),
        }

        if not host.is_remote:
            epp_dir.mkdir(parents=True, exist_ok=True)
            for prefix, (sub, filename) in mapping.items():
                src = self._find_yaml(stack_path, prefix)
                if src and self._has_yaml_content(src):
                    dest = (epp_dir if sub else workspace) / filename
                    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            return None

        # Remote: assemble the exact directory tree locally, then push it in one
        # scp so the daemon host sees a complete workspace or none of it.
        local_stage = context.setup_logs_dir() / f"nok8s-stage-{stack_path.name}"
        (local_stage / "epp").mkdir(parents=True, exist_ok=True)
        for prefix, (sub, filename) in mapping.items():
            src = self._find_yaml(stack_path, prefix)
            if src and self._has_yaml_content(src):
                dest = local_stage / sub / filename if sub else local_stage / filename
                dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        result = cmd.execute(
            host.push_dir(str(local_stage), str(workspace)), check=False
        )
        if not result.success:
            return (
                f"Failed to stage nok8s configs to {host.destination}:{workspace}: "
                f"{(result.stderr or '').strip()[:300]}. Check that SSH to "
                f"{host.destination} works non-interactively "
                f"('ssh {host.destination} true') and that the path is writable."
            )
        context.logger.log_info(
            f"Staged nok8s configs to {host.destination}:{workspace}"
        )
        return None

    def _build_run_command(  # pylint: disable=too-many-arguments
        self,
        c: dict,
        runtime: str,
        workspace: Path,
        epp_dir: Path,
        hf_cache: str,
        hf_token_env: str,
        model: str,
        host: ContainerHost,
        environ=None,
    ) -> tuple[str, str]:
        """Build the docker/podman run command for one container from its spec.

        Returns ``(command, stdin)``; *stdin* carries the Hugging Face token for
        a remote runtime and is empty otherwise (see :meth:`_token_forward`).

        Every path in a ``-v`` flag and every port in a ``-p`` flag is resolved
        by the daemon, so on a remote host they already refer to that node.

        The argument tail is assembled first and handed to ``wrap_runtime`` at
        the end: under ssh transport the command is quoted as one unit, so
        appending to an already-wrapped prefix would leave these flags outside
        the quotes, where the *client's* shell would eat them.
        """
        kind = c["kind"]
        image = c["image"]
        run = "run"
        if kind == "vllm":
            device = self._device_args(runtime, c)
            pin = self._pin_env(c)
            extra = " ".join(c.get("extraArgs") or [])
            # `-e VAR` with no value is expanded by whoever runs the CLI: the
            # local client under the native transport, the node's shell under
            # ssh. Either way the token is never written to the node's disk --
            # for ssh it is piped in, see _token_forward.
            prefix, stdin = self._token_forward(host, hf_token_env, environ)
            tail = (
                f"{run} -d --name {c['name']}"
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
            return host.wrap_runtime(tail, prefix), stdin
        if kind == "epp":
            mount_dir = c.get("configMountDir", "/etc/epp")
            return (
                host.wrap_runtime(
                    f"{run} -d --name {c['name']} --network host "
                    f"-v {epp_dir}:{mount_dir}:ro {image} "
                    f"--config-file={mount_dir}/config.yaml "
                    f"--pool-name={c.get('poolName', 'file-discovery')} "
                    f"--pool-namespace={c.get('poolNamespace', 'default')} "
                    f"--grpc-port={c['grpcPort']} "
                    f"--grpc-health-port={c['grpcHealthPort']} "
                    f"--metrics-port={c['metricsPort']} "
                    f"--secure-serving=false --v=2"
                ),
                "",
            )
        if kind == "envoy":
            mount_path = c.get("configMountPath", "/etc/envoy/envoy.yaml")
            # Envoy's hot-restart shared memory and domain socket are named
            # after the base ID, and --network host makes that name host-wide:
            # with the default 0, a second Envoy on the node exits with
            # errno=98 before it ever binds its listener. The renderer seeds a
            # per-stack ID from listenPort; a plan rendered before it did omits
            # the key, and 0 there means "leave it to Envoy" as it always did.
            base_id = c.get("baseId")
            base = f"--base-id {base_id} " if base_id else ""
            return (
                host.wrap_runtime(
                    f"{run} -d --name {c['name']} --network host "
                    f"-v {workspace / 'envoy.yaml'}:{mount_path}:ro {image} "
                    f"{base}"
                    f"--service-node envoy-proxy --log-level warn --concurrency 8 "
                    f"--drain-strategy immediate --drain-time-s 60 -c {mount_path}"
                ),
                "",
            )
        raise ValueError(f"Unknown nok8s container kind: {kind}")

    @staticmethod
    def _token_forward(
        host: ContainerHost, hf_token_env: str, environ=None
    ) -> tuple[str, str]:
        """``(env_prefix, stdin)`` carrying the HF token to a remote runtime.

        Under the native transport the local CLI expands ``-e VAR`` from this
        process's environment, so nothing is needed. Under ssh transport the
        runtime runs on the node, where that variable is unset -- without this
        a gated model would fail to download and the only symptom would be a
        401 deep in the vLLM log.

        The value goes over stdin rather than in the command, because commands
        are written to the workspace command log and this one is a credential.
        """
        env = os.environ if environ is None else environ
        return host.env_forward_stdin([hf_token_env], env)

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
        self,
        cmd: CommandExecutor,
        host: ContainerHost,
        spec: dict,
        context: ExecutionContext,
    ) -> str | None:
        """Poll vLLM workers then Envoy for /v1/models. Returns error string or None.

        The probe runs *on the daemon host*, so ``localhost`` is the machine
        actually serving. Probing from the client instead would report the
        client's ports and would additionally require every vLLM port to be
        reachable across the network, which is not something a bare-metal node's
        firewall owes us.
        """
        readiness = spec.get("readiness", {})
        ports = list(readiness.get("vllmPorts", [])) + [readiness.get("envoyPort")]
        timeout = context.nok8s_deploy_timeout
        for port in ports:
            if port is None:
                continue
            url = f"http://localhost:{port}/v1/models"
            probe = host.shell(f"curl -fsS {shlex.quote(url)}")
            deadline = time.time() + timeout
            ok = False
            while time.time() < deadline:
                result = cmd.execute(probe, check=False, force=True)
                if result.success:
                    ok = True
                    break
                time.sleep(10)
            if not ok:
                where = f" on {host.destination}" if host.is_remote else ""
                return f"Timed out waiting for {url}{where} after {timeout}s"
            context.logger.log_info(
                f"nok8s endpoint ready: {url}"
                + (f" (on {host.destination})" if host.is_remote else "")
            )
        return None

    def _rollback(
        self,
        cmd: CommandExecutor,
        host: ContainerHost,
        launched: list[str],
        context: ExecutionContext,
    ) -> None:
        """Remove the containers this standup already started, logs first.

        ``rm -f`` destroys the container logs, and a failed standup is exactly
        when they are needed, so every container is dumped before it is
        removed. Removal is in reverse launch order and best-effort: a rollback
        must not mask the failure that triggered it.
        """
        for name in reversed(launched):
            self._dump_logs(cmd, host, name, context)
            cmd.execute(host.runtime_cmd("rm", "-f", name), check=False)
            context.logger.log_info(f"nok8s rollback: removed container {name}")

    def _dump_logs(
        self,
        cmd: CommandExecutor,
        host: ContainerHost,
        name: str,
        context: ExecutionContext,
    ) -> None:
        """Best-effort capture of a container's logs into the setup logs dir.

        ``logs`` goes over the same connection as ``run``, so a remote failure
        is diagnosable from the client without anyone SSHing in -- which is the
        whole point of driving the node remotely.
        """
        result = cmd.execute(
            host.runtime_cmd("logs", name, "--tail", "100"), check=False, force=True
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
