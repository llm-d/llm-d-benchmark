"""Step 07 (nok8s) -- Run the benchmark harness as a container, no cluster.

The default DeployHarnessStep launches the load generator as a Kubernetes Pod.
For the no-Kubernetes (nok8s) method there is no cluster, so this step runs the
same harness image as a docker/podman container, pointed at the Envoy front
door.  Results are written straight to a host results dir via a bind-mount --
no PVC or data-access pod.

The container runs on whichever host ``nok8s.connection`` names, and for a
remote node it runs **there**, not on the client, because the endpoint it
measures is ``http://localhost:<listenPort>`` inside that host's network
namespace (``--network host``).  Driving the load from the client instead would
add the WAN round-trip to every request and report it as the stack's latency.

Running on the node means the three bind-mounted directories -- profiles, the
harness scripts, and the results dir -- have to exist there, so they are pushed
before launch and the results are pulled back after, leaving the caller with
the same local results tree the local path produces.

Reuses DeployHarnessStep's static command/name helpers so the in-container
harness invocation matches the pod path exactly.
"""

import os
import time
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep
from llmdbenchmark.utilities.container_host import (
    ContainerHost,
    ContainerHostError,
    expand_remote_path,
)


class DeployHarnessLocalStep(Step):
    """Run harness container(s) locally against the nok8s endpoint."""

    def __init__(self):
        super().__init__(
            number=7,
            name="deploy_harness_local",
            description="Run benchmark harness as a local container (no k8s)",
            phase=Phase.RUN,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        if context.harness_skip_run:
            return True
        return "nok8s" not in (context.deployed_methods or [])

    def execute(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return self._fail(None, "No stack path provided for per-stack step")

        stack_name = stack_path.name
        cmd = context.require_cmd()
        plan_config = self._load_stack_config(stack_path)
        runtime = context.container_runtime or "docker"
        nok8s_cfg = plan_config.get("nok8s") or {}
        try:
            host = ContainerHost.parse(
                nok8s_cfg.get("connection") or context.container_connection,
                runtime=runtime,
                identity=nok8s_cfg.get("sshIdentity") or "",
                ssh_args=nok8s_cfg.get("sshArgs") or None,
                transport=nok8s_cfg.get("transport") or "",
            )
        except ContainerHostError as exc:
            return self._fail(stack_path, str(exc))

        harness_name = self._resolve(
            plan_config,
            "harness.name",
            context_value=context.harness_name,
            default="inference-perf",
        )
        model_name = self._resolve(
            plan_config, "model.name", context_value=context.model_name, default=""
        )
        endpoint_url = self._resolve_endpoint(context, stack_path, host, stack_name)
        if not endpoint_url:
            return self._fail(stack_path, "No endpoint URL resolved for nok8s run")

        harness_executable = self._resolve(
            plan_config, "harness.executable", default="llm-d-benchmark.sh"
        )
        entrypoint = (plan_config.get("harness") or {}).get(
            "entrypoint", "llm-d-benchmark.sh"
        )
        profile_name = self._resolve(
            plan_config,
            "harness.experimentProfile",
            "harness.profile",
            context_value=context.harness_profile,
            default="sanity_random.yaml",
        )
        if profile_name.endswith(".in"):
            profile_name = profile_name[:-3]

        images = plan_config.get("images", {}).get("benchmark", {})
        image = (
            f"{images.get('repository', 'ghcr.io/llm-d/llm-d-benchmark')}"
            f":{images.get('tag', 'latest')}"
        )
        hf_token_env = (plan_config.get("nok8s") or {}).get(
            "hfTokenEnv", "HUGGING_FACE_HUB_TOKEN"
        )
        deploy_method = ",".join(context.deployed_methods or ["nok8s"])

        base_dir = context.base_dir or Path(__file__).resolve().parents[3]
        harnesses_dir = base_dir / "workload" / "harnesses"
        profiles_dir = context.workload_profiles_dir()
        results_dir = context.run_results_dir()
        timeout = context.harness_wait_timeout

        # Bind-mount sources are resolved by the daemon, so for a remote host
        # the profiles/harnesses/results trees are mirrored under a scratch dir
        # there and the container mounts *those* paths. `mount_*` is what goes
        # into the -v flags; the `*_dir` locals stay client-side, which is where
        # the entry script is written and the results are finally read.
        mount_harnesses, mount_profiles, mount_results = (
            harnesses_dir,
            profiles_dir,
            results_dir,
        )
        remote_root = ""
        if host.is_remote:
            remote_root = self._remote_run_dir(cmd, host, context, stack_name)
            mount_harnesses = Path(remote_root) / "harnesses"
            mount_profiles = Path(remote_root) / "profiles"
            mount_results = Path(remote_root) / "results"
            if not context.dry_run:
                push_err = self._push_inputs(
                    cmd, host, harnesses_dir, profiles_dir, remote_root, context
                )
                if push_err:
                    return self._fail(stack_path, push_err, [push_err])

        treatments = context.experiment_treatments or [None]
        parallelism = context.harness_parallelism
        errors: list[str] = []

        for treatment in treatments:
            experiment_id = self._experiment_id(harness_name, treatment)
            pod_profile_name = (
                DeployHarnessStep._treatment_profile_name(profile_name, treatment)
                if treatment
                else profile_name
            )

            names: list[str] = []
            for i in range(1, parallelism + 1):
                name = f"{harness_name}-{experiment_id}-{i}".lower()
                exp_results = f"/requests/{experiment_id}_{i}"
                if context.harness_debug:
                    harness_command = "sleep infinity"
                else:
                    harness_command = DeployHarnessStep._build_harness_command(
                        harness_executable=harness_executable,
                        profile_name=pod_profile_name,
                        harness_name=harness_name,
                        results_dir=exp_results,
                        entrypoint=entrypoint,
                        dataset_url=context.dataset_url,
                    )

                # Write the in-container entry script to disk and mount it, to
                # avoid shell-quoting issues with the harness command.
                entry_host = results_dir / f".harness-entry-{name}.sh"
                mount_entry = mount_results / entry_host.name
                if not context.dry_run:
                    entry_host.write_text(
                        "#!/bin/sh\n"
                        "for s in /workspace/harnesses/*.sh; do "
                        'cp "$s" /usr/local/bin/ 2>/dev/null; '
                        'chmod +x "/usr/local/bin/$(basename "$s")" 2>/dev/null; '
                        "done\n"
                        f"{harness_command}\n",
                        encoding="utf-8",
                    )
                    # The entry script is a bind-mount source too, so a remote
                    # daemon needs its own copy. Pushed per container because
                    # the command it carries is per container.
                    if host.is_remote:
                        push = cmd.execute(
                            host.push_file(str(entry_host), str(mount_entry)),
                            check=False,
                        )
                        if not push.success:
                            err = (
                                f"Failed to stage the harness entry script to "
                                f"{host.destination}:{mount_entry}: "
                                f"{(push.stderr or '').strip()[:200]}"
                            )
                            errors.append(err)
                            continue

                # Built as an argument tail and wrapped once at the end: under
                # ssh transport the command is quoted as a unit, so appending to
                # an already-wrapped prefix would strand these flags outside the
                # quotes. The HF token is forwarded the same way as in standup.
                token_prefix, token_stdin = host.env_forward_stdin(
                    [hf_token_env], os.environ
                )
                run_tail = (
                    f"run -d --name {name} --network host "
                    # Stage markers are logged in local time, then compared against
                    # `date -u` scrape timestamps when clipping per-stage series.
                    f"-e TZ=UTC "
                    f"-e LLMDBENCH_RUN_EXPERIMENT_LAUNCHER=1 "
                    f"-e LLMDBENCH_HARNESS_STACK_ENDPOINT_URL={endpoint_url} "
                    f"-e LLMDBENCH_HARNESS_STACK_TYPE=vllm-prod "
                    f"-e LLMDBENCH_DEPLOY_CURRENT_MODEL={model_name} "
                    f"-e LLMDBENCH_DEPLOY_CURRENT_TOKENIZER={model_name} "
                    f"-e LLMDBENCH_HARNESS_NAME={harness_name} "
                    f"-e LLMDBENCH_RUN_EXPERIMENT_ID={experiment_id} "
                    f"-e LLMDBENCH_DEPLOY_METHODS={deploy_method} "
                    f"-e LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR_PREFIX=/requests "
                    # Harness reads the profile from
                    # $LLMDBENCH_RUN_WORKSPACE_DIR/profiles/<harness>/<workload>;
                    # keep it in sync with the profiles bind-mount below.
                    f"-e LLMDBENCH_RUN_WORKSPACE_DIR=/workspace "
                    f"-e {hf_token_env} "
                    f"-v {mount_results}:/requests "
                    f"-v {mount_profiles}:/workspace/profiles "
                    f"-v {mount_harnesses}:/workspace/harnesses:ro "
                    f"-v {mount_entry}:/tmp/harness-entry.sh:ro "
                    # Override the image ENTRYPOINT (llm-d-benchmark.sh) so our
                    # setup script runs -- mirrors the k8s pod's command:[sh,-c].
                    f"--entrypoint sh {image} /tmp/harness-entry.sh"
                )
                run_cmd = host.wrap_runtime(run_tail, token_prefix)
                cmd.execute(host.runtime_cmd("rm", "-f", name), check=False)
                result = cmd.execute(run_cmd, check=False, stdin=token_stdin)
                if not result.success and not context.dry_run:
                    errors.append(f"Failed to start harness container {name}")
                else:
                    names.append(name)

            context.logger.log_info(
                f"Launched {len(names)} harness container(s) for "
                f"experiment '{experiment_id}' -> {endpoint_url}"
            )

            if not context.dry_run and names:
                # Block until all containers exit (or the wait times out).
                # `timeout` bounds the client-side wait; the runtime client is
                # what blocks, so this stays local even for a remote daemon.
                cmd.execute(
                    f"timeout {timeout} {host.runtime_cmd('wait', *names)}",
                    check=False,
                    force=True,
                )
                for name in names:
                    # In debug mode the container runs 'sleep infinity', so the
                    # wait always times out -- there is no harness status to read.
                    if not context.harness_debug:
                        error = self._exit_status_error(cmd, host, name, timeout)
                        if error:
                            errors.append(error)
                    self._capture_and_remove(cmd, host, name, results_dir)

                # Bring the results home so the collect/analyze steps and the
                # caller see the same tree the local path produces.
                if host.is_remote:
                    pull = cmd.execute(
                        host.pull_dir(str(mount_results), str(results_dir)),
                        check=False,
                    )
                    if not pull.success:
                        errors.append(
                            f"Failed to fetch results from "
                            f"{host.destination}:{mount_results}: "
                            f"{(pull.stderr or '').strip()[:200]}"
                        )

            context.experiment_ids.append(experiment_id)

        if errors:
            # Non-fatal in the same sense as the k8s wait step: partial results
            # are already on the host via the /requests bind-mount.
            context.logger.log_warning(
                f"Harness container(s) did not complete cleanly; partial "
                f"results and .log files may still be under {results_dir}"
            )
            return self._fail(stack_path, "; ".join(errors), errors)

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Ran {len(context.experiment_ids)} experiment(s) locally "
                f"against {endpoint_url}"
            ),
            stack_name=stack_name,
        )

    # ------------------------------------------------------------------ #
    def _experiment_id(self, harness_name: str, treatment) -> str:
        timestamp = int(time.time())
        rand = DeployHarnessStep._rand_suffix(6)
        tname = treatment.get("name", "") if isinstance(treatment, dict) else ""
        if tname:
            return f"{harness_name}-{tname}-{timestamp}-{rand}"
        return f"{harness_name}-{timestamp}-{rand}"

    @classmethod
    def _resolve_endpoint(cls, context, stack_path: Path, host, stack_name: str) -> str:
        """The URL this harness container should benchmark.

        The container runs *on* the daemon host, so for a remote node it must
        use the in-host ``endpoint`` (``http://localhost:<port>``) rather than
        the client-side ``clientEndpoint`` (``http://<node>:<port>``); dialling
        the node's external address from the node itself adds a hop to every
        request and reports it as the stack's latency.

        Both other sources are client-side by construction: for a ``run`` with
        no standup in the same process, the CLI defaults ``endpoint_url`` to the
        client URL, and step 03 copies that straight into
        ``deployed_endpoints``. So a resolved value equal to the spec's
        ``clientEndpoint`` is that default and gets swapped for the in-host one.
        Anything else came from an explicit ``--endpoint-url`` and is left
        alone, as is every local stack (where the two are identical anyway).
        """
        resolved = context.deployed_endpoints.get(stack_name) or context.endpoint_url
        if not host.is_remote:
            return resolved
        spec = cls._read_endpoints(stack_path)
        in_host, client = spec.get("endpoint", ""), spec.get("clientEndpoint", "")
        # Both sides normalised: the spec's endpoints and a --endpoint-url may
        # differ only by a trailing slash, which is the same target.
        if in_host and (not resolved or (resolved or "").rstrip("/") == client):
            return in_host
        return resolved

    @staticmethod
    def _read_endpoints(stack_path: Path) -> dict:
        """``endpoint``/``clientEndpoint`` from the rendered launch spec.

        Empty when there is no spec (a hand-rolled plan, or a ``--endpoint-url``
        run against a stack this workspace never rendered), which leaves the
        resolved endpoint untouched.
        """
        for spec_file in sorted(stack_path.glob("34_nok8s-containers*")):
            try:
                spec = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                return {}
            return {
                "endpoint": str(spec.get("endpoint") or "").rstrip("/"),
                "clientEndpoint": str(spec.get("clientEndpoint") or "").rstrip("/"),
            }
        return {}

    @staticmethod
    def _remote_run_dir(cmd, host, context, stack_name: str) -> str:
        """Scratch dir on the daemon host holding this run's bind-mount trees.

        Keyed by the workspace directory name, which is unique per invocation
        (``<user>-<timestamp>``), so results are never mixed with an earlier
        run's -- ``run_results_dir()`` is always literally ``results`` and would
        have made every run share one remote directory. The tree is left in
        place after the pull so a failed remote run stays inspectable on the
        node.
        """
        run_id = context.workspace.name or "run"
        home = ""
        if not context.dry_run:
            result = cmd.execute(host.shell("printenv HOME"), check=False, force=True)
            home = (result.stdout or "").strip()
        root = expand_remote_path("~/.llmdbench/nok8s-runs", home)
        return f"{root}/{stack_name}/{run_id}"

    @staticmethod
    def _push_inputs(  # pylint: disable=too-many-arguments
        cmd, host, harnesses_dir: Path, profiles_dir: Path, remote_root: str, context
    ) -> str | None:
        """Mirror the harness inputs to the daemon host. Error string or None.

        Fatal rather than best-effort: docker turns a missing bind-mount source
        into an empty directory, so a silent failure here would start a harness
        with no profile and no scripts, and the run would fail deep inside the
        container instead of here.
        """
        results_remote = f"{remote_root}/results"
        for local, remote in (
            (harnesses_dir, f"{remote_root}/harnesses"),
            (profiles_dir, f"{remote_root}/profiles"),
        ):
            result = cmd.execute(host.push_dir(str(local), remote), check=False)
            if not result.success:
                return (
                    f"Failed to stage {local.name} to {host.destination}:{remote}: "
                    f"{(result.stderr or '').strip()[:300]}"
                )
        # The harness writes into /requests, so the mount source has to exist
        # before the container starts.
        mk = cmd.execute(host.shell(f"mkdir -p {results_remote}"), check=False)
        if not mk.success:
            return (
                f"Failed to create {host.destination}:{results_remote}: "
                f"{(mk.stderr or '').strip()[:300]}"
            )
        context.logger.log_info(
            f"Staged harness inputs to {host.destination}:{remote_root}"
        )
        return None

    def _exit_status_error(self, cmd, host, name, timeout) -> str | None:
        """Return an error string if harness container *name* did not exit 0.

        ``<runtime> wait`` prints the exit code(s) to stdout and exits 0 itself,
        so the wait's own status says nothing about the harness. Ask the runtime
        for the container's terminal state instead. Must be called before
        _capture_and_remove(), which removes the container.
        """
        result = cmd.execute(
            host.runtime_cmd(
                "inspect", "-f", "'{{.State.Status}} {{.State.ExitCode}}'", name
            ),
            check=False,
            force=True,
        )
        fields = (result.stdout or "").strip().split()
        if not result.success or len(fields) != 2:
            return f"Could not read exit status of harness container {name}"

        status, exit_code = fields
        if status != "exited":
            return (
                f"Harness container {name} did not finish within {timeout}s "
                f"(status={status}); raise --wait-timeout or check that "
                f"'{name}.log' shows progress"
            )
        if exit_code != "0":
            return (
                f"Harness container {name} exited {exit_code}; "
                f"see '{name}.log' in the results dir"
            )
        return None

    def _capture_and_remove(self, cmd, host, name, results_dir: Path) -> None:
        """Save the container's log locally, then remove it.

        The log is written to the *client's* results dir even for a remote run,
        so a failed remote run is diagnosable without SSHing to the node. It has
        to be read before the ``rm -f`` below, which destroys it.
        """
        result = cmd.execute(host.runtime_cmd("logs", name), check=False, force=True)
        try:
            (results_dir / f"{name}.log").write_text(
                (result.stdout or "") + (result.stderr or ""), encoding="utf-8"
            )
        except OSError:
            pass
        cmd.execute(host.runtime_cmd("rm", "-f", name), check=False)

    def _fail(self, stack_path, message, errors=None) -> StepResult:
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=False,
            message=message,
            errors=errors or [message],
            stack_name=stack_path.name if stack_path else None,
        )
