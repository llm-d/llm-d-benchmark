"""Step 00 -- Validate system dependencies and print cluster summary banner."""

import os
from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.deps import (
    OPTIONAL_TOOL_CONSEQUENCE,
    check_system_dependencies,
    check_python_version,
    check_helm_version,
    check_helmfile_version,
    MIN_HELM_MAJOR,
    MIN_HELMFILE_VERSION,
)
from llmdbenchmark.utilities.cluster import print_phase_banner
from llmdbenchmark.utilities.container_host import (
    NATIVE,
    SSH,
    ContainerHost,
    ContainerHostError,
)

# Listening-socket probes, first one available wins. ss is iproute2 (Linux);
# lsof covers macOS and hosts without iproute2.
PORT_PROBES = ("ss -ltn", "lsof -nP -iTCP -sTCP:LISTEN")

# Signatures of a runtime client being refused by the node's sshd. Worth
# recognising because the generic "check your ssh" advice is actively wrong for
# them: podman's Go SSH client does not read ~/.ssh/id_rsa the way OpenSSH does,
# so `ssh <dest> true` can succeed while every container command fails.
_SSH_AUTH_SIGNATURES = (
    "unable to authenticate",
    "handshake failed",
    "no supported methods remain",
    "permission denied",
)

# Which client wrote the failure. This is more reliable than asking the binary
# its version, because `docker` is frequently a shim for podman (the
# podman-docker package, an alias, a DOCKER_HOST pointing at podman's socket)
# and podman accepts docker's -H as a synonym for --url, so the substitution is
# invisible until something fails. The wording, though, is not transferable:
# podman dials SSH in-process with Go's x/crypto/ssh and surfaces its handshake
# text, while docker's CLI execs the system `ssh` binary and can only report
# that process's exit status. So whichever family the text belongs to is the
# client that really ran, whatever nok8s.runtime says.
_GO_SSH_SIGNATURES = (
    "handshake failed",
    "attempted methods",
    "no supported methods remain",
    "unable to connect to podman socket",
)
_DOCKER_SHELLOUT_SIGNATURES = (
    "has exited with exit status",
    "docker system dial-stdio",
    "error during connect",
)

# Why the text is attributable, quoted back at the user so the claim that their
# 'docker' is really podman comes with its evidence rather than as an assertion.
_WHO_SPEAKS = {
    "podman": (
        "podman dials SSH itself and reports Go's handshake wording, whereas "
        "docker execs the ssh binary and could only report its exit status"
    ),
    "docker": (
        "docker execs the ssh binary and reports its exit status, whereas "
        "podman dials SSH itself and would report Go's handshake wording"
    ),
}


# ssh exits 255 for its own failures (refused, timed out, auth rejected) and
# passes the remote command's status through otherwise. A probe that comes back
# 255 therefore says nothing about the node, so it must not be read as "the tool
# is missing there".
SSH_FAILURE_EXIT = 255


def _speaker_of(stderr: str) -> str:
    """Which client family wrote *stderr*: ``podman``, ``docker`` or ``""``.

    See ``_GO_SSH_SIGNATURES``: the two clients cannot produce each other's
    SSH-failure wording, so their own text identifies them even when the binary
    that was invoked claims otherwise.
    """
    lowered = (stderr or "").lower()
    if any(sig in lowered for sig in _GO_SSH_SIGNATURES):
        return "podman"
    if any(sig in lowered for sig in _DOCKER_SHELLOUT_SIGNATURES):
        return "docker"
    return ""


def _version_flavor(cmd, runtime: str) -> str:
    """What the *runtime* binary says it is: ``podman``, ``docker`` or ``""``.

    Empty when the probe cannot run or prints something unrecognised -- e.g. a
    wrapper script, or a client whose version goes to a pager. That is why this
    is only ever corroborating evidence for :func:`_speaker_of`, never the
    deciding one.
    """
    result = cmd.execute(f"{runtime} --version", check=False, force=True, silent=True)
    text = f"{result.stdout or ''} {result.stderr or ''}".lower()
    if "podman" in text:
        return "podman"
    if "docker" in text:
        return "docker"
    return ""


def _client_flavor(cmd, runtime: str, stderr: str = "") -> tuple[str, str]:
    """The client that actually ran, and how that was established.

    Returns ``(flavor, evidence)``. *evidence* is ``"error"`` when the failure
    text gave it away, ``"version"`` when only ``--version`` did, and ``""``
    when neither could -- in which case *flavor* is empty and the caller must
    not make a claim about which client is on the PATH.

    The error text wins when the two disagree. It was written by the process
    that failed; ``--version`` only describes whichever binary the shell
    resolved, which is exactly what a podman shim installed as ``docker``
    misrepresents.
    """
    spoke = _speaker_of(stderr)
    reported = _version_flavor(cmd, runtime)
    if spoke:
        return spoke, "error"
    if reported:
        return reported, "version"
    return "", ""


def _daemon_hints(  # pylint: disable=too-many-arguments
    runtime: str, flavor: str, evidence: str, identity: str, stderr: str
) -> str:
    """Guidance for a daemon that would not answer, tailored to the client.

    The generic advice ("check that ssh works") is misleading in the two cases
    this untangles: the client is not the one configured, and the client is
    podman refusing to authenticate even though ssh itself is fine.

    *flavor* is what actually ran and *evidence* how that was determined, both
    from :func:`_client_flavor`. Advice is keyed on *flavor*, not on *runtime*,
    because a podman shim installed as ``docker`` needs podman's advice -- the
    earlier version keyed on ``runtime`` and so handed the user docker's advice
    alongside podman's own error text.
    """
    hints: list[str] = []
    lowered = (stderr or "").lower()
    if flavor and flavor != runtime:
        because = (
            f" -- {_WHO_SPEAKS[flavor]}"
            if evidence == "error" and flavor in _WHO_SPEAKS
            else ""
        )
        hints.append(
            f"the '{runtime}' on PATH is actually {flavor}{because}. It accepts "
            f"docker's -H as a synonym for --url, so the mismatch surfaces here "
            f"rather than as a bad flag -- set nok8s.runtime={flavor} to match "
            f"the client and its socket path"
        )
    if any(sig in lowered for sig in _SSH_AUTH_SIGNATURES):
        if flavor == "podman" or (not flavor and runtime == "podman"):
            if identity:
                # A key was supplied and still refused, so the fix is on the
                # node, not in the configuration.
                hints.append(
                    "podman does its own SSH auth, so a working 'ssh' says "
                    "nothing about it: the key in nok8s.sshIdentity was offered "
                    "and refused -- check it is in the node's authorized_keys "
                    "for that user"
                )
            else:
                hints.append(
                    "podman does its own SSH auth and, unlike ssh, never falls "
                    "back to ~/.ssh/id_rsa -- with no key from "
                    "nok8s.sshIdentity, CONTAINER_SSHKEY, ssh-agent, or 'podman "
                    "system connection add' it offers none at all (hence "
                    "'attempted methods [none]'), so a working 'ssh' proves "
                    "nothing. Pass nok8s.sshIdentity=<path-to-private-key>"
                )
        elif flavor == "docker" or runtime == "docker":
            hints.append(
                "docker shells out to your system ssh, so the key must be in the "
                "agent ('ssh-add <key>') or IdentityFile in ~/.ssh/config -- "
                "nok8s.sshIdentity does not reach the docker client"
            )
        else:
            # Neither the error text nor --version identified the client, so
            # naming one would be a guess; give the advice for both.
            hints.append(
                f"'{runtime}' could not be identified as docker or podman, and "
                f"they authenticate differently: podman needs a key it can see "
                f"(nok8s.sshIdentity=<path>), docker needs one your system ssh "
                f"can see ('ssh-add <key>')"
            )
    if not hints:
        hints.append(
            f"check that '{runtime}' is running on the node and that the SSH user "
            f"may use it (docker group, or rootless podman with "
            f"'systemctl --user enable --now podman.socket')"
        )
    return "; ".join(hints) + "."


def _as_port(value, default: int) -> int | None:
    """Coerce a configured nok8s port to an int, or None if it isn't one.

    Scenario YAML has no type validation, so a quoted port (``listenPort:
    "8081"``) reaches here as a string and used to crash this preflight with
    an unhandled ``TypeError`` from ``sorted()``. Digit strings are accepted
    the way ``RenderPlans._validate_nok8s_host_claims`` accepts them, so the
    two agree on what counts as a port. Anything else returns None and is
    reported as unverifiable rather than aborting a warnings-only preflight.

    A missing key falls back to *default*; an explicitly empty value (``None``)
    does too, matching Jinja's use of the default for an unset field.
    """
    if value is None:
        return default
    if isinstance(value, bool):  # bool is an int subclass; not a port.
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _as_count(value, default: int = 1) -> int:
    """Coerce a configured replica/tensor-parallel count, falling back to *default*.

    Same untyped-YAML problem as ``_as_port``, but these values only size a
    GPU-capacity warning, so an unusable one degrades to *default* instead of
    being reported separately.
    """
    count = _as_port(value, default)
    return default if count is None or count < 1 else count


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
                    cost = OPTIONAL_TOOL_CONSEQUENCE.get(tool)
                    context.logger.log_warning(
                        f"Optional tool not found: {tool}"
                        + (f" -- {cost}" if cost else "")
                    )

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
        # The accelerator, the ports and the host tools all have to be present on
        # the machine that will run the containers, so every probe below is
        # wrapped in host.shell(). For a local stack that wrapper is the
        # identity function and the checks are exactly what they were.
        try:
            host = ContainerHost.parse(
                nok8s.get("connection") or context.container_connection,
                runtime=runtime,
                identity=nok8s.get("sshIdentity") or "",
                ssh_args=nok8s.get("sshArgs") or None,
                transport=nok8s.get("transport") or "",
            )
        except ContainerHostError as exc:
            context.logger.log_error(f"    {exc}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="nok8s infrastructure checks failed",
                errors=[str(exc)],
            )
        # Every nok8s stack in the plan lands on this host, so the port and
        # GPU checks cover all of them, not just the first.
        stacks = [
            cfg
            for cfg in (
                (self._load_stack_config(p).get("nok8s") or {})
                for p in (context.rendered_stacks or [])
            )
            if cfg.get("enabled")
        ] or [nok8s]
        ports, bad_ports = self._nok8s_ports(stacks)

        if context.dry_run:
            context.logger.log_info(
                f"[dry-run] nok8s preflight: would verify '{runtime}' runtime, "
                f"'{accelerator}' accelerator, free ports {ports}, and ${hf_env} "
                f"on {host.destination}."
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
        # Set when the runtime could not reach a remote daemon: every probe
        # below travels the same SSH connection, so continuing would turn one
        # connection failure into a list of bogus "not found on the node" errors.
        remote_unreachable = False

        # 1. Container runtime present and usable (fatal). Where the *client*
        #    has to be depends on the transport: the native transport carries the
        #    connection in a local client binary, while ssh transport invokes the
        #    runtime on the node, so nothing is required here but `ssh`. Either
        #    way `info` is answered by the daemon, so the call also proves the
        #    connection works end to end.
        if (
            host.needs_local_runtime
            and not cmd.execute(
                f"command -v {runtime}", check=False, force=True, silent=True
            ).success
        ):
            errors.append(
                f"Container runtime client '{runtime}' not found on PATH. Install "
                f"docker or podman (or set nok8s.runtime to the one you have)."
                + (
                    f" This is only needed because nok8s.transport is "
                    f"'{NATIVE}'; the default '{SSH}' transport runs "
                    f"'{runtime}' on the node instead and needs no local client."
                    if host.is_remote
                    else ""
                )
            )
        else:
            info = cmd.execute(
                host.runtime_cmd("info"), check=False, force=True, silent=True
            )
            if not info.success and host.uses_ssh:
                # Under ssh transport there is no local client to misidentify:
                # the command ran (or failed to run) on the node. ssh's own 255
                # separates the two halves cleanly -- a connection that never
                # opened, versus a runtime the SSH user cannot drive -- so the
                # client-flavour diagnosis below does not apply here, and naming
                # a socket path would be doubly misleading since none is used.
                stderr = (info.stderr or "").strip()
                if info.exit_code == SSH_FAILURE_EXIT:
                    errors.append(
                        f"Cannot reach {host.destination} over ssh: "
                        f"{stderr[:300]}. Check that 'ssh {host.destination} true' "
                        f"succeeds without a prompt -- key-based auth (passwords "
                        f"are not supported), the host key already in "
                        f"known_hosts, and a key ssh can see (ssh-agent, "
                        f"~/.ssh/config, or nok8s.sshIdentity)."
                    )
                else:
                    errors.append(
                        f"Reached {host.destination}, but '{runtime}' there did "
                        f"not answer: {stderr[:300]}. Verify with 'ssh "
                        f"{host.destination} {runtime} info'. Usually the daemon "
                        f"is down, or the SSH user cannot use it -- add them to "
                        f"the 'docker' group, or for rootless podman run "
                        f"'systemctl --user enable --now podman.socket'."
                    )
                remote_unreachable = True
            elif not info.success and host.is_remote:
                stderr = (info.stderr or "").strip()
                flavor, evidence = _client_flavor(cmd, runtime, stderr)
                errors.append(
                    f"Cannot reach the '{runtime}' daemon at {host.url}: "
                    f"{stderr[:300]}. Check that 'ssh {host.destination} true' "
                    f"succeeds without a prompt (key-based auth, host key already "
                    f"known); if it does, the client is at fault, not ssh: "
                    + _daemon_hints(runtime, flavor, evidence, host.identity, stderr)
                )
                # The connection is what failed, so the per-tool probes below
                # would each report the node's tools as missing. Skip them and
                # let this one error stand.
                remote_unreachable = True
            elif not info.success:
                errors.append(
                    f"'{runtime}' is installed but not usable (daemon down or "
                    f"permissions). Verify with '{runtime} info'."
                )

        # 1b. Host tools the nok8s path shells out to (fatal). The k8s
        #     toolchain check does not run for nok8s, so these would otherwise go
        #     unchecked -- but each has to be checked where it actually runs:
        #
        #     * `curl` probes endpoint readiness *on the node* (step 06), so it
        #       is looked for there.
        #     * `timeout` bounds the harness wait in step 07 by wrapping the
        #       command the *client* runs -- `ssh ...` or the local client
        #       binary -- so it is needed here, not on the node. Probing it
        #       remotely (as this did) passed on a node that had it while the
        #       client did not, and the wait then ran unbounded.
        for tool, on_node in (("timeout", False), ("curl", True)):
            if remote_unreachable and on_node:
                break
            probe_target = (
                host.shell(f"command -v {tool}") if on_node else (f"command -v {tool}")
            )
            probe = cmd.execute(probe_target, check=False, force=True, silent=True)
            if on_node and host.is_remote and probe.exit_code == SSH_FAILURE_EXIT:
                # ssh could not run anything, so the node's PATH is unknown.
                # Report the connection once instead of every tool.
                errors.append(
                    f"Cannot run commands on {host.destination}: "
                    f"{(probe.stderr or '').strip()[:200] or 'ssh exited 255'}. "
                    f"The node's tools, accelerator and ports were not checked. "
                    f"Verify 'ssh {host.destination} true' succeeds without a "
                    f"prompt."
                )
                remote_unreachable = True
                break
            if not probe.success:
                where = f" on {host.destination}" if on_node and host.is_remote else ""
                errors.append(
                    f"'{tool}' not found on PATH{where}; the nok8s path needs it "
                    f"(install GNU coreutils and curl)."
                )
        if host.is_remote:
            for tool in ("ssh", "scp"):
                if not cmd.execute(
                    f"command -v {tool}", check=False, force=True, silent=True
                ).success:
                    errors.append(
                        f"'{tool}' not found on PATH; a remote nok8s connection "
                        f"needs it to stage configs and probe the node."
                    )

        # 2. Accelerator visible on the host (warning). Probe depends on the
        #    configured accelerator; cpu/spyre/custom are not probed here.
        probe = {
            "nvidia": "nvidia-smi -L",
            "amd": "rocm-smi --showid",
            "intel": "xpu-smi discovery",
            "gaudi": "hl-smi -L",
        }.get(accelerator)
        if probe and remote_unreachable:
            context.logger.log_info(
                "    skipping accelerator probe: the node was unreachable."
            )
        elif probe:
            if not cmd.execute(
                host.shell(probe), check=False, force=True, silent=True
            ).success:
                tool = probe.split()[0]
                where = f" on {host.destination}" if host.is_remote else ""
                warnings.append(
                    f"'{tool}' found no {accelerator} accelerator{where}; vLLM "
                    f"needs the device + driver present, the matching vLLM image, "
                    f"and the container toolkit configured for '{runtime}'."
                )
        else:
            context.logger.log_info(
                f"    accelerator='{accelerator}': skipping device probe "
                f"(cpu/spyre/custom -- ensure nok8s.vllm.deviceArgs + image match)."
            )

        # 2b. GPU capacity: replicas x tensorParallel must fit the host's GPUs.
        #     Only checkable for nvidia (nvidia-smi -L enumerates devices).
        needed = sum(
            _as_count(cfg.get("vllm", {}).get("replicas", 1))
            * _as_count(cfg.get("vllm", {}).get("tensorParallel", 1))
            for cfg in stacks
        )
        if accelerator == "nvidia" and needed > 1 and not remote_unreachable:
            res_gpu = cmd.execute(
                host.shell("nvidia-smi -L"), check=False, force=True, silent=True
            )
            if res_gpu.success and res_gpu.stdout:
                count = sum(
                    1 for ln in res_gpu.stdout.splitlines() if ln.startswith("GPU ")
                )
                if count and needed > count:
                    warnings.append(
                        f"nok8s.vllm needs {needed} GPUs (replicas x tensorParallel "
                        f"over {len(stacks)} stack(s)) but only {count} detected -- "
                        f"workers will contend for devices or fail to start."
                    )

        # 2c. A worker with replicas: 1 and no explicit device selection gets
        #     the whole accelerator ("--gpus all"), so two such stacks claim
        #     the same devices and the second one runs out of memory.
        unpinned = [
            cfg
            for cfg in stacks
            if _as_count(cfg.get("vllm", {}).get("replicas", 1)) == 1
            and str(cfg.get("vllm", {}).get("gpus", "all")) == "all"
            and not cfg.get("vllm", {}).get("deviceArgs")
        ]
        if accelerator != "cpu" and len(unpinned) > 1:
            warnings.append(
                f"{len(unpinned)} nok8s stacks each take every accelerator on this "
                f"host; give them distinct devices with nok8s.vllm.gpus (docker) or "
                f"nok8s.vllm.deviceArgs, or they will fight over memory."
            )

        # 3. Hugging Face token (warning).
        if not os.environ.get(hf_env):
            warnings.append(
                f"${hf_env} is not set; gated Hugging Face models will fail to "
                f"download in the vLLM container."
            )

        # 4. Required host ports free (warning). Pick the first probe tool that
        #    exists; warn rather than pass silently when none does.
        if bad_ports:
            warnings.append(
                f"Not a whole number, so not checked: {', '.join(bad_ports)}. "
                f"These must be plain integers; a quoted value in the scenario "
                f"YAML (listenPort: '8081') is a string, not a number."
            )
        probe_cmd = next(
            (
                p
                for p in PORT_PROBES
                if cmd.execute(
                    host.shell(f"command -v {p.split()[0]}"),
                    check=False,
                    force=True,
                    silent=True,
                ).success
            ),
            None,
        )
        if remote_unreachable:
            pass  # Already reported; the ports on an unreachable node are moot.
        elif probe_cmd is None:
            warnings.append(
                f"Cannot verify host ports {ports}: none of "
                f"{', '.join(p.split()[0] for p in PORT_PROBES)} found on PATH"
                + (f" on {host.destination}" if host.is_remote else "")
                + ". Install iproute2 or lsof, or check the ports yourself."
            )
        else:
            res = cmd.execute(
                host.shell(probe_cmd), check=False, force=True, silent=True
            )
            busy = [p for p in ports if f":{p} " in (res.stdout or "")]
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

        context.logger.log_info(f"nok8s preflight passed ({host.describe()}).")
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"nok8s infrastructure ready ({host.describe()})",
        )

    # Config path -> whether the value is the base of `vllm.replicas`
    # consecutive ports. Mirrors RenderPlans._NOK8S_HOST_PORTS.
    _NOK8S_PORT_FIELDS: tuple[tuple[str, str, int, bool], ...] = (
        ("vllm", "hostPort", 8000, True),
        ("envoy", "listenPort", 8081, False),
        ("envoy", "adminPort", 19000, False),
        ("epp", "grpcPort", 9002, False),
        ("epp", "grpcHealthPort", 9003, False),
        ("epp", "metricsPort", 9090, False),
    )

    def _nok8s_ports(self, stacks: list[dict]) -> tuple[list[int], list[str]]:
        """Host ports every nok8s stack in the plan will claim.

        Returns ``(ports, bad)`` where *bad* names the fields whose configured
        value is not a port number. Those are skipped rather than raised: this
        preflight is warnings-only, and a bad value is already reported by the
        render, so crashing here would replace a clear render error with a
        traceback.
        """
        ports: set[int] = set()
        bad: list[str] = []
        for cfg in stacks:
            replicas = _as_port(cfg.get("vllm", {}).get("replicas", 1), 1)
            if replicas is None or replicas < 1:
                if replicas is None:
                    bad.append("nok8s.vllm.replicas")
                replicas = 1
            for section, field, default, is_base in self._NOK8S_PORT_FIELDS:
                port = _as_port(cfg.get(section, {}).get(field, default), default)
                if port is None:
                    bad.append(f"nok8s.{section}.{field}")
                    continue
                # One worker per replica, hostPort .. hostPort+replicas-1
                # (34_nok8s-containers.yaml.j2).
                span = replicas if is_base else 1
                ports.update(range(port, port + span))
        return sorted(ports), sorted(set(bad))
