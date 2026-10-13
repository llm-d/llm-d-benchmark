"""Teardown Step 03 -- Delete namespaced resources (normal or deep mode)."""

import json
from pathlib import Path

from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.step import Phase, Step, StepResult

NORMAL_RESOURCE_LIST = (
    "daemonset,disaggregatedset,leaderworkerset,deployment,statefulset,httproute,service,"
    "gateway,gatewayparameters,"
    "inferencepool,inferencemodel,resourceclaimtemplate,configmap,ingress,pod,job"
)

FMA_RESOURCE_LIST = (
    "replicaset,inferenceserverconfig,launcherconfig,launcherpopulationpolicy"
)

SYSTEM_EXCLUDES = {
    "kube-root-ca.crt",
    "odh-trusted-ca-bundle",
    "openshift-service-ca.crt",
}

# llm-d-prism persists across normal teardown; deep clean or --prism removes it.
PRISM_PERSIST_LABEL = "llm-d-benchmark.ai/persist=true"
PRISM_NAME_MATCH = "llm-d-prism"
PRISM_APP_LABEL = "app.kubernetes.io/name=llm-d-prism"

STANDALONE_PATTERNS = [
    "standalone",
    "download-model",
    "download-model-ds",
    "testinference",
    "lmbenchmark",
]

# Model download workloads (the download Job, and the hostPath download
# DaemonSet). Standup creates them for every deploy method, so they are
# deleted whichever method filter applies. "download-model" is the shipped
# name prefix of both (multi-stack names add a -<model_id_label> suffix);
# names a scenario configures are read from the plan.
DOWNLOAD_KINDS = ("job.batch/", "job/", "daemonset.apps/", "daemonset/")
DOWNLOAD_NAME_PREFIX = "download-model"

MODELSERVICE_PATTERNS = [
    "llm-d-benchmark-preprocesses",
    "p2p",
    "inference-gateway",
    "inferencepool",
    "httproute",
    "inferencepools.inference.networking.k8s.io",
    "llm-route",
    "base-model",
    "endpoint-picker",
    "inference-route",
    "inference-gateway-secret",
    "inference-gateway-params",
    "lmbenchmark",
    # Catches the GAIE/EPP Deployment (named e.g. "<model>-router-epp" or
    # the older "<model>-gaie-epp" convention) as a safety net independent
    # of Helm release matching -- "endpoint-picker" above only matches the
    # container image repo, not the Deployment's own object name.
    "-epp",
    # Plain-Service baseline created for gateway.className=none.
    "-direct",
]

DEEP_RESOURCE_KINDS = [
    "disaggregatedset",
    "leaderworkerset",
    "deployment",
    "statefulset",
    "service",
    "secret",
    "gateway",
    "inferencemodel",
    "inferencepool",
    "httproute",
    "resourceclaimtemplate",
    "configmap",
    "daemonset",
    "job",
    "role",
    "rolebinding",
    "serviceaccount",
    "hpa",
    "va",
    "servicemonitor",
    "podmonitor",
    "pod",
    "pvc",
]

OPENSHIFT_RESOURCE_KINDS = ["route"]


class DeleteResourcesStep(Step):
    """Delete namespaced resources (normal or deep mode)."""

    def __init__(self):
        super().__init__(
            number=3,
            name="delete_resources",
            description="Delete namespaced resources",
            phase=Phase.TEARDOWN,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "nok8s" in (context.deployed_methods or [])

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        namespaces = self._all_target_namespaces(context)

        if context.deep_clean:
            self._deep_clean(cmd, context, namespaces, errors)
        else:
            self._normal_clean(cmd, context, namespaces, errors)

        if errors:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Resource deletion had errors",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                f"Namespaced resources deleted "
                f"({'deep' if context.deep_clean else 'normal'} mode)"
            ),
        )

    def _deep_clean(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespaces: list[str],
        errors: list,
    ):
        """Delete all resources of each kind in both namespaces."""
        kinds = list(DEEP_RESOURCE_KINDS)
        if context.is_openshift:
            kinds.extend(OPENSHIFT_RESOURCE_KINDS)

        for ns in namespaces:
            context.logger.log_info(
                f'Deep cleaning namespace "{ns}" ({len(kinds)} resource kinds)...'
            )
            for kind in kinds:
                list_result = cmd.kube(
                    "get",
                    kind,
                    "--namespace",
                    ns,
                    "-o",
                    "name",
                    "--no-headers",
                    "--ignore-not-found",
                    check=False,
                )
                existing = []
                if list_result.success and list_result.stdout.strip():
                    existing = list_result.stdout.strip().splitlines()

                if not existing:
                    continue

                for resource in existing:
                    context.logger.log_info(f"  Deleting {resource}", emoji="🗑️")

                # check=False suppresses CommandExecutor ERROR logs;
                # we handle failures ourselves below
                delete_args = [
                    "delete",
                    kind,
                    "--all",
                    "--namespace",
                    ns,
                    "--ignore-not-found=true",
                ]
                # Force-delete pods to avoid hanging on Terminating pods
                if kind == "pod":
                    delete_args += ["--grace-period=0", "--force"]
                result = cmd.kube(*delete_args, check=False)
                if not result.success:
                    stderr_lower = result.stderr.lower()
                    if (
                        "the server doesn't have a resource type" in stderr_lower
                        or "not found" in stderr_lower
                        or "no matches for kind" in stderr_lower
                    ):
                        # Resource type doesn't exist on this cluster
                        continue
                    context.logger.log_error(
                        f"Failed to delete {kind} in {ns}: {result.stderr}"
                    )
                    errors.append(f"Failed to delete {kind} in {ns}: {result.stderr}")
                else:
                    context.logger.log_info(
                        f"  Deleted {len(existing)} {kind}(s) in {ns}"
                    )

        self._delete_host_path_pvs(cmd, context, namespaces)

    def _normal_clean(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespaces: list[str],
        errors: list,
    ):
        """Query for resources, filter by deployment method, delete individually."""
        resource_list = NORMAL_RESOURCE_LIST
        if context.is_openshift:
            resource_list += ",route"

        fma_active = "fma" in context.deployed_methods
        if fma_active:
            resource_list += f",{FMA_RESOURCE_LIST}"

        resource_list = self._prune_unsupported(cmd, resource_list, namespaces[0])

        standalone_active = "standalone" in context.deployed_methods
        modelservice_active = "modelservice" in context.deployed_methods

        plan_config = self._load_plan_config(context)
        if not plan_config:
            raise KeyError(
                "Required plan config not found. Cannot determine HF token "
                "secret name for resource filtering."
            )
        hf_secret = self._require_config(plan_config, "huggingface", "secretName")
        download_names = self._download_workload_names(context)

        for ns in namespaces:
            context.logger.log_info(f'Cleaning namespace "{ns}" (normal mode)...')

            result = cmd.kube(
                "get",
                resource_list,
                "--namespace",
                ns,
                "-o",
                "name",
            )
            if not result.success or not result.stdout.strip():
                all_resources = []
            else:
                all_resources = result.stdout.strip().splitlines()

            filtered = [
                r for r in all_resources if not self._is_system_resource(r, hf_secret)
            ]

            candidates = filtered
            if standalone_active and not modelservice_active:
                filtered = [
                    r for r in filtered if self._matches_any(r, STANDALONE_PATTERNS)
                ]
            elif modelservice_active and not standalone_active:
                filtered = [
                    r for r in filtered if self._matches_any(r, MODELSERVICE_PATTERNS)
                ]
            kept = set(filtered)
            filtered += [
                r
                for r in candidates
                if r not in kept and self._is_download_workload(r, download_names)
            ]

            # Also find model-serving workload controllers by pod
            # template labels. oc get -l only filters by metadata.labels,
            # but the llm-d.ai/inferenceServing label is on the pod
            # template, so we query as JSON and filter in Python.
            workload_result = cmd.kube(
                "get",
                "leaderworkerset,deployment,statefulset",
                "--namespace",
                ns,
                "-o",
                "json",
                "--ignore-not-found",
                check=False,
            )
            if workload_result.success and workload_result.stdout.strip():
                try:
                    items = json.loads(workload_result.stdout).get("items", [])
                    existing = set(filtered)
                    for item in items:
                        spec = item.get("spec", {})
                        # Deployment/StatefulSet: spec.template.metadata.labels
                        # LeaderWorkerSet: spec.leaderWorkerTemplate.workerTemplate.metadata.labels
                        tmpl_labels = (
                            spec.get("template", {})
                            .get("metadata", {})
                            .get("labels", {})
                        )
                        if not tmpl_labels.get("llm-d.ai/inferenceServing"):
                            tmpl_labels = (
                                spec.get("leaderWorkerTemplate", {})
                                .get("workerTemplate", {})
                                .get("metadata", {})
                                .get("labels", {})
                            )
                        if tmpl_labels.get("llm-d.ai/inferenceServing") != "true":
                            continue
                        # Use apiVersion/kind to build the resource name
                        # that oc delete accepts (e.g.,
                        # "leaderworkerset.leaderworkerset.x-k8s.io/name")
                        api_version = item.get("apiVersion", "")
                        kind = item.get("kind", "")
                        name = item.get("metadata", {}).get("name", "")
                        # For core/apps resources (Deployment, StatefulSet),
                        # "deployment/name" works. For CRDs, use the full
                        # group-qualified form from apiVersion.
                        if "/" in api_version and not api_version.startswith("apps/"):
                            group = api_version.split("/")[0]
                            resource = f"{kind.lower()}.{group}/{name}"
                        else:
                            resource = f"{kind.lower()}/{name}"
                        if resource and resource not in existing:
                            filtered.append(resource)
                            existing.add(resource)
                except json.JSONDecodeError, KeyError:
                    pass

            # Preserve persistent llm-d-prism resources (skipped on deep clean);
            # --prism deletes them below by label.
            if not context.deep_clean:
                protected = self._prism_protected_names(cmd, resource_list, ns)
                before = len(filtered)
                filtered = [
                    r
                    for r in filtered
                    if r not in protected and PRISM_NAME_MATCH not in r.lower()
                ]
                skipped = before - len(filtered)
                if skipped:
                    context.logger.log_info(
                        f"  Preserving {skipped} persistent llm-d-prism "
                        f"resource(s) in {ns} (removed by `teardown --prism` or `-d`)"
                    )

            if context.remove_prism:
                self._delete_prism(cmd, context, ns, errors)

            if not filtered:
                context.logger.log_info(f"  No matching resources found in {ns}")
                continue

            context.logger.log_info(
                f"  Found {len(filtered)} resource(s) to delete in {ns}"
            )
            deleted_count = 0
            for resource in filtered:
                context.logger.log_info(f"  Deleting {resource}", emoji="🗑️")
                delete_args = [
                    "delete",
                    "--namespace",
                    ns,
                    "--ignore-not-found=true",
                    resource,
                ]
                # Force-delete pods to avoid hanging on Terminating pods
                if resource.startswith("pod/"):
                    delete_args += ["--grace-period=0", "--force"]
                del_result = cmd.kube(*delete_args)
                if del_result.success:
                    deleted_count += 1
                else:
                    stderr_lower = del_result.stderr.lower()
                    if "not found" in stderr_lower or "no matches" in stderr_lower:
                        context.logger.log_info(f"    Already removed: {resource}")
                    else:
                        context.logger.log_warning(
                            f"    Could not delete {resource}: {del_result.stderr}"
                        )

            context.logger.log_info(
                f"  Deleted {deleted_count}/{len(filtered)} resources in {ns}"
            )

        self._delete_host_path_pvs(cmd, context, namespaces)

    @staticmethod
    def _delete_prism(
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        errors: list,
    ) -> None:
        """Delete the persistent llm-d-prism dashboard (`teardown --prism`).

        Matched by its app label rather than name, since the name is
        configurable. The Route created by `oc expose` inherits the Service's
        labels. The workload PVC it mounts holds results and is kept.
        """
        kinds = "deployment,service"
        if context.is_openshift:
            kinds += ",route"
        context.logger.log_info(
            f"  Deleting llm-d-prism dashboard in {namespace} (--prism)",
            emoji="🗑️",
        )
        result = cmd.kube(
            "delete",
            kinds,
            "--namespace",
            namespace,
            "-l",
            PRISM_APP_LABEL,
            "--ignore-not-found=true",
            check=False,
        )
        if not result.success:
            context.logger.log_warning(
                f"    Could not delete llm-d-prism in {namespace}: {result.stderr}"
            )
            errors.append(
                f"Failed to delete llm-d-prism in {namespace}: {result.stderr}"
            )

    @staticmethod
    def _delete_host_path_pvs(
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespaces: list[str],
    ) -> None:
        """Delete the hostPath PVs bound to our own namespaces.

        Standup no longer creates these (hostPath mode mounts the node
        directory directly); this cleans up PVs left by older standups.

        PVs are cluster-scoped and `usage=model-cache` carries no namespace, so
        on a shared cluster the label alone also matches other tenants' PVs. One
        of those left Terminating behind a pv-protection finalizer never goes
        away, and a plain delete waits for it forever. Match on claimRef instead
        and bound the wait.
        """
        mine = set(filter(None, namespaces))
        if not mine:
            return

        result = cmd.kube(
            "get",
            "pv",
            "-l",
            "usage=model-cache",
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{" "}'
            '{.spec.claimRef.namespace}{"\\n"}{end}',
            check=False,
        )
        if not result.success or not result.stdout.strip():
            return

        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            claim_ns = parts[1] if len(parts) > 1 else ""
            if claim_ns not in mine:
                context.logger.log_info(
                    f"  Keeping pv/{name}: bound to {claim_ns or '<unbound>'}, not ours"
                )
                continue
            context.logger.log_info(f"  Deleting pv/{name}", emoji="🗑️")
            del_result = cmd.kube(
                "delete",
                f"pv/{name}",
                "--ignore-not-found",
                # A finalizer that never clears would otherwise hang teardown.
                "--timeout=60s",
                check=False,
            )
            if not del_result.success:
                context.logger.log_warning(
                    f"  Could not delete pv/{name}: {del_result.stderr.strip()}"
                )

    def _prune_unsupported(
        self, cmd: CommandExecutor, resource_list: str, namespace: str
    ) -> str:
        """Remove resource types that the cluster does not support."""
        supported = []
        for resource_type in resource_list.split(","):
            resource_type = resource_type.strip()
            if not resource_type:
                continue
            result = cmd.kube(
                "get",
                resource_type,
                "--namespace",
                namespace,
                "--no-headers",
                "-o",
                "name",
                check=False,
            )
            if result.success or "No resources found" in result.stderr:
                supported.append(resource_type)
        return ",".join(supported)

    @staticmethod
    def _prism_protected_names(
        cmd: CommandExecutor, resource_list: str, namespace: str
    ) -> set[str]:
        """Return `name/...` strings of persistent llm-d-prism resources."""
        result = cmd.kube(
            "get",
            resource_list,
            "--namespace",
            namespace,
            "-l",
            PRISM_PERSIST_LABEL,
            "-o",
            "name",
            "--ignore-not-found",
            check=False,
        )
        if result.success and result.stdout.strip():
            return set(result.stdout.strip().splitlines())
        return set()

    @staticmethod
    def _is_system_resource(resource: str, hf_secret: str) -> bool:
        """Return True if this resource should be preserved."""
        resource_lower = resource.lower()
        for exclude in SYSTEM_EXCLUDES:
            if exclude in resource_lower:
                return True
        if f"secret/{hf_secret}" in resource_lower:
            return True
        return False

    def _download_workload_names(self, context: ExecutionContext) -> set[str]:
        """Download Job / DaemonSet names configured by any rendered stack."""
        names: set[str] = set()
        for stack_path in context.rendered_stacks or []:
            cfg = self._load_stack_config(stack_path) or {}
            names.add((cfg.get("downloadJob") or {}).get("name") or "")
            hostpath = (cfg.get("storage") or {}).get("hostPath") or {}
            names.add(hostpath.get("daemonSetName") or "")
        names.discard("")
        return names

    @staticmethod
    def _is_download_workload(resource: str, names: set[str]) -> bool:
        """Return True for a model download Job or DaemonSet."""
        kind_prefix = next(
            (k for k in DOWNLOAD_KINDS if resource.lower().startswith(k)), None
        )
        if kind_prefix is None:
            return False
        name = resource[len(kind_prefix) :]
        return name in names or name.lower().startswith(DOWNLOAD_NAME_PREFIX)

    @staticmethod
    def _matches_any(resource: str, patterns: list[str]) -> bool:
        """Return True if the resource name matches any of the patterns."""
        resource_lower = resource.lower()
        return any(p.lower() in resource_lower for p in patterns)
