"""Step 06 -- Deploy Fast Model Actuation Controllers."""

import json
import shlex
from pathlib import Path
from datetime import datetime, timezone

import yaml

from llmdbenchmark import __version__
from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor


class FMADeployStep(Step):
    """Deploy Fast Model Actuation Controllers."""

    def __init__(self):
        super().__init__(
            number=6,
            name="fma_deploy",
            description="Deploy Fast Model Actuation Controllers.",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "fma" not in context.deployed_methods

    def execute(  # pylint: disable=too-many-branches,too-many-locals
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        if stack_path is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No stack path provided for per-stack step",
                errors=["stack_path is required"],
            )

        errors = []
        cmd = context.require_cmd()

        plan_config = self._load_stack_config(stack_path)
        namespace = context.require_namespace()

        clusterrole_yaml = self._find_yaml(stack_path, "25_fma-clusterrole")
        if not clusterrole_yaml or not self._has_yaml_content(clusterrole_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No FMA ClusterRole YAML found, skipping",
                errors=["FMA ClusterRole YAML is required"],
                stack_name=stack_path.name,
            )

        fma_helmfile = self._find_yaml(stack_path, "26_helmfile-fma-controllers")
        if not fma_helmfile or not self._has_yaml_content(fma_helmfile):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No FMA Controllers helm values found, skipping",
                errors=["FMA Controllers helm values are required"],
                stack_name=stack_path.name,
            )

        deploy_yaml = self._find_yaml(stack_path, "24_fma-deployment")
        if not deploy_yaml or not self._has_yaml_content(deploy_yaml):
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="No FMA deployment YAML found, skipping",
                errors=["FMA deployment YAML is required"],
                stack_name=stack_path.name,
            )

        # Fast Model Actuation CRDS
        self._install_fma_crds(
            context, plan_config, errors
        )  # pylint disable=too-many-function-args

        # Fast Model Actuation ClusterRole
        self._install_fma_clusterole(context, clusterrole_yaml, errors)

        if len(errors) == 0:
            # Fast Model Actuation Controllers chart
            result = cmd.helmfile(
                "apply",
                "-f",
                str(fma_helmfile),
                "--skip-diff-on-install",
            )
            if not result.success:
                errors.append(
                    f"Failed to apply FMA controllets helmfile: {result.stderr}"
                )

        if len(errors) == 0:
            # Wait for fma dual pod to be created, running, and ready
            label_selector = (
                "app.kubernetes.io/name=fma-controllers,"
                "app.kubernetes.io/component=dual-pods-controller"
            )
            wait_result = cmd.wait_for_pods(
                label=label_selector,
                namespace=namespace,
                timeout=900,
                poll_interval=10,
                description="FMA Dual Pod Controller",
            )
            if not wait_result.success:
                errors.append(
                    f"Standalone deployment pods not ready: {wait_result.stderr}"
                )

        if len(errors) == 0:
            # Wait for fma launcher populator pod to be created, running, and ready
            label_selector = (
                "app.kubernetes.io/name=fma-controllers,"
                "app.kubernetes.io/component=launcher-populator"
            )
            wait_result = cmd.wait_for_pods(
                label=label_selector,
                namespace=namespace,
                timeout=900,
                poll_interval=10,
                description="FMA Launcher Populator",
            )
            if not wait_result.success:
                errors.append(
                    f"Standalone deployment pods not ready: {wait_result.stderr}"
                )

        # Optionally pick a dedicated GPU node and label it, so the LPP and
        # requester (which select on that label when launcherNodeSelection is
        # enabled) land only there. Runs before applying the deployment.
        # Returns the node's GPU count so we can size the requester replica
        # count to it (one launcher/requester pair per GPU) after apply.
        selected_gpu_count: int | None = None
        if len(errors) == 0 and self._launcher_node_selection_enabled(plan_config):
            selected_gpu_count = self._select_and_label_gpu_node(
                context, cmd, plan_config, errors
            )

        if len(errors) == 0:
            # Apply deployment
            result = cmd.kube("apply", "-f", str(deploy_yaml))
            if not result.success:
                errors.append(f"Failed to apply fma deployment: {result.stderr}")

        # Size FMA to the selected node's GPU count: LauncherPopulationPolicy
        # launcherCount + requester replicas + KEDA ScaledObject ceiling. The
        # rendered values are placeholders that may exceed one node's capacity;
        # a bound launcher/requester pair shares one GPU, so we want exactly
        # gpu_count launchers AND gpu_count requesters. Only when node selection
        # ran and yielded a count.
        if len(errors) == 0 and selected_gpu_count:
            model_id_label = plan_config.get("model_id_label", "")
            node_label = self._node_label(plan_config)
            self._size_fma_to_gpu_count(
                context,
                cmd,
                namespace,
                model_id_label,
                selected_gpu_count,
                node_label,
                stack_path,
                errors,
            )

        if len(errors) == 0:
            resource_types = (
                "InferenceServerConfig,LauncherConfig,"
                "LauncherPopulationPolicy,deployment,replicaset,pods"
            )
            cmd.kube(
                "get",
                resource_types,
                "--namespace",
                namespace,
            )

        if len(errors) == 0:
            # Wait for at least one launcher pod to be Bound (carrying the
            # ISC labels propagated by the dual-pods-controller). Without
            # this wait, standup returns immediately after the requester
            # is Ready -- but the dual-pods-controller binds a launcher
            # ASYNCHRONOUSLY (typically 30s to several minutes after
            # requester Ready).
            #
            # Skip the wait when fma.requester.replicas == 0. When node
            # selection resized the requester to the node's GPU count, use that
            # effective count instead of the rendered placeholder.
            requester_replicas = selected_gpu_count or (
                plan_config.get("fma", {}).get("requester", {}).get("replicas", 0)
            )
            model_id_label = plan_config.get("model_id_label", "")
            if requester_replicas == 0:
                context.logger.log_info(
                    "    | Skipping bound-launcher wait: "
                    "fma.requester.replicas=0 (no requester pod to bind to)."
                )
            elif model_id_label:
                label_selector = (
                    f"llm-d.ai/inferenceServing=true,llm-d.ai/model={model_id_label}"
                )
                wait_result = cmd.wait_for_pods(
                    label=label_selector,
                    namespace=namespace,
                    timeout=600,
                    poll_interval=10,
                    description=f"FMA bound launcher (model={model_id_label})",
                )
                if not wait_result.success:
                    errors.append(
                        f"FMA bound launcher pod did not become Ready in "
                        f"ns/{namespace}: {wait_result.stderr}. The "
                        f"dual-pods-controller may not have bound a "
                        f"launcher to the requester yet."
                    )

        self._propagate_standup_parameters(cmd, context, plan_config)

        if len(errors) > 0:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="FMA deployment had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(f"FMA deployment applied from {stack_path.name}"),
            stack_name=stack_path.name,
        )

    @staticmethod
    def _launcher_node_selection(plan_config: dict) -> dict:
        """The fma.launcherNodeSelection block, or {} (guards an explicit null)."""
        return (plan_config.get("fma", {}) or {}).get("launcherNodeSelection", {}) or {}

    @classmethod
    def _launcher_node_selection_enabled(cls, plan_config: dict) -> bool:
        """True when fma.launcherNodeSelection.enabled is set for this stack."""
        return bool(cls._launcher_node_selection(plan_config).get("enabled", False))

    @classmethod
    def _node_label(cls, plan_config: dict) -> str:
        """The node label key to use. `or` guards an explicit null/empty value
        (the .get default only applies to a MISSING key, not an explicit null)."""
        return (
            cls._launcher_node_selection(plan_config).get("nodeLabel") or "fma-hotstart"
        )

    def _select_and_label_gpu_node(
        self,
        context: ExecutionContext,
        cmd: CommandExecutor,
        plan_config: dict,
        errors: list[str],
    ) -> int | None:
        """Pick a dedicated GPU node and label it for FMA launcher selection.

        A node qualifies only if it (a) advertises a single GPU product type
        (``nvidia.com/gpu.product`` label), (b) has FREE GPUs -- allocatable
        minus the GPUs already requested by pods on the node (NOT
        ``allocatable == capacity``, which ignores GPUs consumed by other
        tenants), AND (c) has enough free CPU (allocatable minus committed CPU
        requests) for one requester per free GPU plus a margin, so we don't pick
        a CPU-saturated node (which triggers OpenShift's "CPU limits approaching
        capacity" throttling warning). Nodes failing any check are skipped in
        favor of another GPU node.

        Among qualifying nodes, a fully-free (dedicated -- no GPUs claimed by
        any other pod) node is preferred over a partially-used one, then most
        free GPUs, then most free CPU. The chosen node is labeled
        ``<nodeLabel>=true`` so the LauncherPopulationPolicy and requester
        Deployment -- which select on that label when launcherNodeSelection is
        enabled -- land only there.

        Returns the selected node's FREE GPU count (so the caller sizes the
        launcher/requester counts to it -- one requester per free GPU), or
        ``None`` if no node qualified (fatal error appended, standup fails).
        """
        node_label = self._node_label(plan_config)

        # In dry-run, `kube` short-circuits to empty stdout; force the read so
        # node selection can actually inspect the cluster. Skip entirely if the
        # executor is in dry-run and cannot reach a cluster.
        result = cmd.kube("get", "nodes", "-o", "json", check=False, force=True)
        if not result.success or not (result.stdout or "").strip():
            errors.append(
                "FMA launcher node selection is enabled but listing cluster "
                f"nodes failed: {result.stderr or 'no output from get nodes'}"
            )
            return None

        try:
            nodes = json.loads(result.stdout).get("items", [])
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Failed to parse `get nodes -o json`: {exc}")
            return None

        # Sum CPU + GPU requests of all non-terminal pods per node. A node's
        # .status.allocatable is TOTAL capacity and does NOT drop as pods consume
        # the resource, so real headroom is allocatable - committed.
        committed = self._committed_resources_by_node(cmd, errors)
        if committed is None:
            return None

        gpu_resource = "nvidia.com/gpu"
        gpu_product_label = "nvidia.com/gpu.product"
        # Reserve one CPU per GPU (each requester requests ~1 CPU) plus a margin
        # so the node is not driven to its CPU limit.
        cpu_margin_m = 2000  # 2 cores of slack
        # (is_dedicated, free_gpu, free_cpu_m, node_name, product)
        candidates = []
        for node in nodes:
            meta = node.get("metadata", {}) or {}
            status = node.get("status", {}) or {}
            name = meta.get("name", "")
            labels = meta.get("labels", {}) or {}
            product = labels.get(gpu_product_label)
            if not name or not product:
                continue  # not a (single-type) GPU node

            allocatable = status.get("allocatable", {}) or {}
            node_committed = committed.get(name, {"cpu_m": 0, "gpu": 0})
            try:
                alloc_gpu = int(allocatable.get(gpu_resource, 0))
            except (TypeError, ValueError):
                continue

            # (a) single GPU type; (b) GPUs actually FREE = allocatable minus
            # GPUs already requested by pods on this node (NOT allocatable ==
            # capacity, which ignores current consumption by other tenants).
            free_gpu = alloc_gpu - node_committed["gpu"]
            if free_gpu <= 0:
                if alloc_gpu > 0:
                    context.logger.log_info(
                        f"    | Skipping GPU node {name}: no free GPUs "
                        f"({node_committed['gpu']}/{alloc_gpu} already in use)"
                    )
                continue

            # (c) enough free CPU: allocatable - committed >= 1 CPU/GPU + margin.
            alloc_cpu_m = self._cpu_to_millicores(allocatable.get("cpu"))
            free_cpu_m = alloc_cpu_m - node_committed["cpu_m"]
            needed_cpu_m = free_gpu * 1000 + cpu_margin_m
            if free_cpu_m < needed_cpu_m:
                context.logger.log_info(
                    f"    | Skipping GPU node {name}: insufficient free CPU "
                    f"({free_cpu_m}m free < {needed_cpu_m}m needed for {free_gpu} "
                    "requesters + margin)"
                )
                continue

            # Dedicated = no GPUs on this node are claimed by any pod, so FMA
            # gets the whole node to itself (preferred for a clean hotstart run).
            is_dedicated = node_committed["gpu"] == 0
            candidates.append((is_dedicated, free_gpu, free_cpu_m, name, product))

        if not candidates:
            errors.append(
                "No target GPU node available for FMA launcher node selection: "
                "need a node whose GPUs are all one type "
                f"({gpu_product_label} present) with FREE GPUs (allocatable minus "
                "GPUs already requested by other pods) AND enough free CPU for "
                "one requester per free GPU plus margin. All GPU nodes are "
                "currently occupied."
            )
            return None

        # Prefer a fully-free (dedicated) node, then most free GPUs, then most
        # free CPU; deterministic tie-break by name. `is_dedicated` True sorts
        # first (not c[0] negated -> use `not`).
        candidates.sort(key=lambda c: (not c[0], -c[1], -c[2], c[3]))
        is_dedicated, gpu_count, free_cpu_m, node_name, product = candidates[0]

        # Best-effort: clear the label from any node that still carries it (from
        # a crashed run or skipped teardown) so exactly ONE node ends up
        # labeled. `<key>-` removes the key; `-l <key>=true` restricts to nodes
        # that have it (no-op when none). Not fatal -- labeling the chosen node
        # below is what matters.
        clear_result = cmd.kube(
            "label",
            "nodes",
            "-l",
            f"{node_label}=true",
            f"{node_label}-",
            check=False,
        )
        if not clear_result.success:
            context.logger.log_warning(
                f"    | Could not clear stale {node_label}=true labels before "
                f"selecting {node_name}: {clear_result.stderr}"
            )

        label_result = cmd.kube(
            "label",
            "node",
            node_name,
            f"{node_label}=true",
            "--overwrite",
            check=False,
        )
        if not label_result.success:
            errors.append(
                f"Failed to label GPU node {node_name} with {node_label}=true: "
                f"{label_result.stderr}"
            )
            return None

        context.logger.log_info(
            f"    | Selected {'dedicated ' if is_dedicated else ''}GPU node "
            f"{node_name} ({gpu_count} free {product} GPU(s); {free_cpu_m}m free "
            f"CPU{'' if is_dedicated else ', shared with other GPU workloads'}) "
            f"for FMA launchers; labeled {node_label}=true"
        )
        return gpu_count

    @staticmethod
    def _cpu_to_millicores(value) -> int:
        """Parse a k8s CPU quantity ("2", "0.5", "500m") into integer millicores."""
        if value is None:
            return 0
        s = str(value).strip()
        if not s:
            return 0
        try:
            if s.endswith("m"):
                return int(float(s[:-1]))
            return int(float(s) * 1000)
        except ValueError:
            return 0

    def _committed_resources_by_node(
        self, cmd: CommandExecutor, errors: list[str]
    ) -> dict[str, dict[str, int]] | None:
        """Sum CPU (millicores) and GPU requests of scheduled, non-terminal pods
        per node.

        Returns ``{node_name: {"cpu_m": int, "gpu": int}}``, or ``None`` (with a
        fatal error appended) if the pod list cannot be read. Needed because a
        node's ``.status.allocatable`` is its TOTAL schedulable capacity and does
        NOT decrease as pods consume the resource -- so real headroom is
        ``allocatable - committed``, computed here.
        """
        result = cmd.kube(
            "get", "pods", "--all-namespaces", "-o", "json", check=False, force=True
        )
        if not result.success or not (result.stdout or "").strip():
            errors.append(
                "FMA launcher node selection: could not list pods to compute "
                f"resource headroom: {result.stderr or 'no output from get pods'}"
            )
            return None
        try:
            pods = json.loads(result.stdout).get("items", [])
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Failed to parse `get pods -o json`: {exc}")
            return None

        gpu_resource = "nvidia.com/gpu"
        committed: dict[str, dict[str, int]] = {}
        for pod in pods:
            spec = pod.get("spec", {}) or {}
            node = spec.get("nodeName")
            if not node:
                continue  # unscheduled -- not committed to any node
            phase = (pod.get("status", {}) or {}).get("phase", "")
            if phase in ("Succeeded", "Failed"):
                continue  # terminal pods do not hold resources
            acc = committed.setdefault(node, {"cpu_m": 0, "gpu": 0})
            for container in spec.get("containers", []) or []:
                res = container.get("resources", {}) or {}
                requests = res.get("requests", {}) or {}
                limits = res.get("limits", {}) or {}
                acc["cpu_m"] += self._cpu_to_millicores(requests.get("cpu"))
                # For extended resources like GPUs, k8s treats a limit as an
                # implicit request; count whichever is present.
                gpu_val = requests.get(gpu_resource, limits.get(gpu_resource))
                try:
                    acc["gpu"] += int(gpu_val) if gpu_val is not None else 0
                except (TypeError, ValueError):
                    pass
        return committed

    def _free_gpu_on_labeled_node(
        self, cmd: CommandExecutor, node_label: str, errors: list[str]
    ) -> int | None:
        """Return currently-free GPUs on the node(s) carrying ``<node_label>=true``.

        Free = allocatable minus GPUs already requested by pods on the node.
        Returns the minimum across matching nodes (normally exactly one), or
        ``None`` (with a fatal error appended) if the node/pod state can't be
        read. Used as a TOCTOU re-check before scaling requesters up.
        """
        gpu_resource = "nvidia.com/gpu"
        node_result = cmd.kube(
            "get",
            "nodes",
            "-l",
            f"{node_label}=true",
            "-o",
            "json",
            check=False,
            force=True,
        )
        if not node_result.success or not (node_result.stdout or "").strip():
            errors.append(
                f"Could not re-read labeled node ({node_label}=true) to verify "
                f"free GPUs before scaling requesters: {node_result.stderr}"
            )
            return None
        try:
            labeled_nodes = json.loads(node_result.stdout).get("items", [])
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Failed to parse labeled `get nodes -o json`: {exc}")
            return None
        if not labeled_nodes:
            errors.append(
                f"No node found with label {node_label}=true when re-verifying "
                "free GPUs (was it unlabeled?)."
            )
            return None

        committed = self._committed_resources_by_node(cmd, errors)
        if committed is None:
            return None

        free_counts = []
        for node in labeled_nodes:
            name = (node.get("metadata", {}) or {}).get("name", "")
            allocatable = (node.get("status", {}) or {}).get("allocatable", {}) or {}
            try:
                alloc_gpu = int(allocatable.get(gpu_resource, 0))
            except (TypeError, ValueError):
                alloc_gpu = 0
            used = committed.get(name, {"gpu": 0})["gpu"]
            free_counts.append(alloc_gpu - used)
        return min(free_counts) if free_counts else 0

    def _size_fma_to_gpu_count(
        self,
        context: ExecutionContext,
        cmd: CommandExecutor,
        namespace: str,
        model_id_label: str,
        gpu_count: int,
        node_label: str,
        stack_path: Path,
        errors: list[str],
    ) -> None:
        """Size FMA to the pinned node's GPU count: one launcher AND one
        requester per GPU (a bound launcher/requester pair shares one GPU).

        The LauncherPopulationPolicy ``launcherCount`` and the requester
        Deployment ``replicas`` are rendered into 24_fma-deployment.yaml as
        placeholders. Once everything is pinned to a single node we must create
        exactly ``gpu_count`` launchers so the ``gpu_count`` requesters each have
        a launcher to bind to; more requesters than launchers leaves the extras
        unbindable, and more of either than GPUs leaves pods Pending with
        ``Insufficient nvidia.com/gpu``.

        The effective count is also written back to the stack's rendered
        ``config.yaml`` (``fma.requester.replicas``) so downstream run/smoketest
        steps that read that value (e.g. the hot-start warmup's rollout wait and
        scale-down/sleeping count) use the actual pinned-node count instead of
        the placeholder.

        Launchers are created BEFORE requesters: the requester Deployment is
        scaled to 0 first, then the LPP launcherCount is set and launchers are
        waited on, then -- after RE-VERIFYING the labeled node still has enough
        free GPUs (another tenant may have claimed some since selection) --
        requesters are scaled up to bind to ready launchers. Also lowers the KEDA
        ScaledObject ceiling to match. Applies only when node selection ran
        (hotstart flag).
        """
        if not model_id_label:
            errors.append("Cannot size FMA to GPU count: model_id_label missing.")
            return

        deploy_name = f"fma-requester-{model_id_label}"
        lpp_name = f"fma-{model_id_label}"
        scaledobject_name = f"{model_id_label}-fma"

        # (1) Hold requesters at 0 first, so the launchers (created next) come up
        # before any requester tries to bind. The requester Deployment was
        # applied with a placeholder replicas>0, so it is already creating pods;
        # scale it down before populating launchers.
        scale_down = cmd.kube(
            "scale",
            f"deployment/{deploy_name}",
            "--replicas=0",
            "--namespace",
            namespace,
            check=False,
        )
        if not scale_down.success:
            errors.append(
                f"Failed to scale {deploy_name} to 0 before launcher population: "
                f"{scale_down.stderr}"
            )
            return

        # (2) Create one launcher per GPU (LauncherPopulationPolicy.countForLauncher
        # is a single-entry list, so patch index 0's launcherCount). Requesters
        # bind 1:1 to launchers, so this must match the requester replica count.
        lpp_patch_body = json.dumps(
            [
                {
                    "op": "replace",
                    "path": "/spec/countForLauncher/0/launcherCount",
                    "value": gpu_count,
                }
            ]
        )
        lpp_patch = cmd.kube(
            "patch",
            f"launcherpopulationpolicy.fma.llm-d.ai/{lpp_name}",
            "--namespace",
            namespace,
            "--type",
            "json",
            "-p",
            # cmd.kube space-joins args into a shell command, so the JSON
            # (quotes/spaces/braces) must be shell-quoted or the shell mangles it.
            shlex.quote(lpp_patch_body),
            check=False,
        )
        if not lpp_patch.success:
            errors.append(
                f"Failed to set LauncherPopulationPolicy {lpp_name} "
                f"launcherCount to {gpu_count} (node GPU count): "
                f"{lpp_patch.stderr}"
            )
            return

        # (3) Wait for the launcher pods to be Ready before creating requesters,
        # so requesters have launchers to bind to as soon as they come up.
        launcher_wait = cmd.wait_for_pods(
            label="app.kubernetes.io/component=launcher",
            namespace=namespace,
            timeout=900,
            poll_interval=10,
            description=f"FMA launchers ({gpu_count} expected, one per GPU)",
        )
        if not launcher_wait.success:
            errors.append(
                f"FMA launcher pods did not become Ready in ns/{namespace} "
                f"before requester creation: {launcher_wait.stderr}"
            )
            return

        # (3b) TOCTOU guard: re-verify the labeled node still has >= gpu_count
        # free GPUs right before creating requesters. Another tenant may have
        # claimed GPUs on it since selection; scheduling gpu_count requesters
        # (each 1 GPU) into fewer free GPUs would leave the excess Pending with
        # Insufficient nvidia.com/gpu. Launchers hold no GPU, so this reflects
        # only other workloads (requesters are still at 0 here).
        free_gpu = self._free_gpu_on_labeled_node(cmd, node_label, errors)
        if free_gpu is None:
            return  # error already appended
        if free_gpu < gpu_count:
            errors.append(
                f"FMA launcher node selection: the labeled node ({node_label}"
                f"=true) now has only {free_gpu} free GPU(s) but {gpu_count} "
                "requesters are needed -- another workload claimed GPUs after "
                "node selection. Aborting to avoid Pending requesters."
            )
            return

        # (4) Now scale requesters up to one pod per GPU (1:1 with launchers).
        scale = cmd.kube(
            "scale",
            f"deployment/{deploy_name}",
            f"--replicas={gpu_count}",
            "--namespace",
            namespace,
            check=False,
        )
        if not scale.success:
            errors.append(
                f"Failed to scale {deploy_name} to {gpu_count} replicas "
                f"(node GPU count): {scale.stderr}"
            )
            return

        # Lower/raise the KEDA ScaledObject ceiling to match so WVA cannot scale
        # the requester beyond the node's GPU capacity. Best-effort: the
        # ScaledObject may not exist yet (rendered/applied separately); a
        # missing object is not fatal to standup.
        patch = cmd.kube(
            "patch",
            f"scaledobject.keda.sh/{scaledobject_name}",
            "--namespace",
            namespace,
            "--type",
            "merge",
            "-p",
            shlex.quote(json.dumps({"spec": {"maxReplicaCount": gpu_count}})),
            check=False,
        )
        if not patch.success and "not found" not in (patch.stderr or "").lower():
            context.logger.log_warning(
                f"    | Could not patch ScaledObject {scaledobject_name} "
                f"maxReplicaCount to {gpu_count}: {patch.stderr}"
            )

        # Persist the effective count so run-phase / smoketest steps that read
        # fma.requester.replicas (e.g. the hot-start rollout wait and sleeping
        # count) use it instead of the rendered placeholder.
        self._persist_requester_replicas(context, stack_path, gpu_count)

        context.logger.log_info(
            f"    | Sized FMA to {gpu_count} launcher/requester pairs "
            f"(LauncherPopulationPolicy {lpp_name} launcherCount + "
            f"{deploy_name} replicas), one pair per GPU on the pinned node"
        )

    @staticmethod
    def _persist_requester_replicas(
        context: ExecutionContext, stack_path: Path, replicas: int
    ) -> None:
        """Write fma.requester.replicas into the stack's config.yaml so later
        steps (loaded via _load_stack_config) read the pinned-node count.

        Best-effort: a write failure is a warning, not fatal -- the live
        Deployment is already scaled correctly; only downstream config-derived
        counts would be stale.
        """
        config_file = stack_path / "config.yaml"
        try:
            with open(config_file, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            cfg.setdefault("fma", {}).setdefault("requester", {})["replicas"] = replicas
            with open(config_file, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh, default_flow_style=False, sort_keys=False)
            context.logger.log_info(
                f"    | Updated fma.requester.replicas={replicas} in "
                f"{config_file.name} (pinned-node GPU count)"
            )
        except (OSError, yaml.YAMLError) as exc:
            context.logger.log_warning(
                f"    | Could not persist fma.requester.replicas={replicas} to "
                f"{config_file}: {exc}"
            )

    def _install_fma_crds(
        self, context: ExecutionContext, plan_config: dict, errors: list[str]
    ) -> None:
        if context.non_admin:
            errors.append(
                "❗No privileges to setup Fast Model Actuation API crds. "
                "Will assume a user with proper privileges already performed this action."
            )
            return

        crds = plan_config.get("fma", {}).get("crds", {})
        crd_urls = {
            "inferenceserverconfigs.fma.llm-d.ai": crds.get(
                "inferenceServerConfig", ""
            ),
            "launcherconfigs.fma.llm-d.ai": crds.get("launcherConfig", ""),
            "launcherpopulationpolicies.fma.llm-d.ai": crds.get(
                "launcherPopulatorConfig", ""
            ),
        }
        cmd = context.require_cmd()
        result = cmd.kube(
            "get", "crd", "-o", "jsonpath='{.items[*].metadata.name}'", check=False
        )
        if not result.success:
            errors.append(f"Failed to query crds: {result.stderr}")
            return

        crd_names = result.stdout.strip().split()
        for name in crd_names:
            if name in crd_urls:
                del crd_urls[name]
                context.logger.log_info(
                    f"✅ Kubernetes Fast Fast Model Actuation CRD {name} already installed"
                )

        errors = []
        for name, url in crd_urls.items():
            context.logger.log_info(f"🚀 Fast Fast Model Actuation API {name} CRD...")
            result = cmd.kube("apply", "--server-side", "-f", url)
            if not result.success:
                errors.append(f"Failed to apply crd '{name}': {result.stderr}")
                continue
            context.logger.log_info(
                f"✅ Fast Fast Model Actuation API {name} CRD installed"
            )

    def _install_fma_clusterole(
        self, context: ExecutionContext, clusterrole_yaml: Path, errors: list[str]
    ) -> None:
        cmd = context.require_cmd()
        result = cmd.kube(
            "get", "clusterroles", "-o", "jsonpath='{.items[*].metadata.name}'"
        )
        if not result.success:
            errors.append(f"Failed to query clusterroles: {result.stderr}")
            return

        clusterrole_names = result.stdout.strip().split()
        for name in clusterrole_names:
            if name == "fma-node-viewer":
                context.logger.log_info(
                    f"✅ Kubernetes Fast Fast Model Actuation ClusterRole {name} already installed"
                )
                return

        context.logger.log_info("🚚 Deploying Fast Model Actuation ClusterRole ...")

        result = cmd.kube("apply", "-f", str(clusterrole_yaml))
        if not result.success:
            errors.append(f"Failed to apply fma clusterrole: {result.stderr}")
            return

        context.logger.log_info("✅ Fast Model Actuation ClusterRole installed")

    def _propagate_standup_parameters(
        self, cmd: CommandExecutor, context: ExecutionContext, plan_config: dict
    ):
        """Persist deploy metadata as a ConfigMap so run-phase steps can read it."""

        harness_ns = context.harness_namespace or context.require_namespace()
        cm_name = "llm-d-benchmark-standup-parameters"

        params = {
            "tool_name": "llm-d-benchmark",
            "tool_version": __version__,
            "deployed_by": context.username or "unknown",
            "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cluster_name": context.cluster_name or "",
            "platform_type": context.platform_type,
            "namespace": context.namespace or "",
            "harness_namespace": harness_ns,
            "deploy_methods": ",".join(context.deployed_methods),
        }

        if plan_config:
            params["model_name"] = self._require_config(plan_config, "model", "name")
            params["model_short_name"] = self._require_config(
                plan_config, "model", "shortName"
            )
            params["model_huggingface_id"] = plan_config.get("model", {}).get(
                "huggingfaceId", ""
            )
            params["inference_port"] = str(
                self._require_config(plan_config, "vllmCommon", "inferencePort")
            )
            params["release"] = self._require_config(plan_config, "release")
            params["standalone_replicas"] = str(
                self._require_config(plan_config, "standalone", "replicas")
            )

        literal_args = []
        for key, value in params.items():
            literal_args.append(f"--from-literal={key}={value}")

        create_args = (
            [
                "create",
                "configmap",
                cm_name,
                "--namespace",
                harness_ns,
            ]
            + literal_args
            + ["--dry-run=client", "-o", "yaml"]
        )

        result = cmd.kube(*create_args)
        if result.success:
            yaml_path = context.setup_yamls_dir() / "standup-parameters.yaml"
            yaml_path.write_text(result.stdout, encoding="utf-8")
            apply_result = cmd.kube("apply", "-f", str(yaml_path))
            if apply_result.success:
                context.logger.log_info(
                    f"📋 Deployment metadata to configmap/{cm_name} in ns/{harness_ns}"
                )
                context.logger.log_info(
                    f"   oc get configmap {cm_name} -n {harness_ns} -o yaml"
                )
