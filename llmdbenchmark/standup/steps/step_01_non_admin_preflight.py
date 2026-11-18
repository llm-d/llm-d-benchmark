"""Step 01 -- Verify the invoking user's permissions for --non-admin."""

from pathlib import Path

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor
from llmdbenchmark.standup.scc import (
    elevated_service_account,
    scenario_requests_elevation,
)
from llmdbenchmark.utilities.endpoint import resolve_direct_service_namespace

# Permissions the router chart grants to the EPP service account. Creating
# that role requires holding them, so a missing one fails the deploy later.
_CHART_GRANTED_RESOURCES = (
    "inferencepools.inference.networking.k8s.io",
    "inferenceobjectives.llm-d.ai",
    "inferencemodelrewrites.llm-d.ai",
)

# The only custom resource the deploy always creates. Looked up through
# discovery: `kubectl get crd` needs cluster-scope rights a namespace
# admin does not have.
_REQUIRED_API_GROUP = "inference.networking.k8s.io"


class NonAdminPreflightStep(Step):
    """Check, as the invoking user, everything a non-admin deploy needs."""

    def __init__(self):
        super().__init__(
            number=1,
            name="non_admin_preflight",
            description="Verify non-admin permissions before deploying",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        # the probes run through the executor, which fakes them in dry-run
        return (
            not context.non_admin
            or "nok8s" in (context.deployed_methods or [])
            or context.dry_run
        )

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors: list[str] = []
        cmd = context.require_cmd()

        model_namespaces = self._model_namespaces(context)

        # the router chart deploys only for modelservice stacks behind
        # a gateway, into the gateway namespace
        chart_namespaces: list[str] = []
        if "modelservice" in (context.deployed_methods or []):
            for rendered in context.rendered_stacks:
                cfg = self._load_stack_config(rendered)
                if not (cfg.get("modelservice", {}) or {}).get("enabled", False):
                    continue
                gateway = cfg.get("gateway", {}) or {}
                if gateway.get("className") == "none":
                    continue
                ns = gateway.get("namespace") or (
                    (cfg.get("namespace", {}) or {}).get("name")
                )
                if ns and ns not in chart_namespaces:
                    chart_namespaces.append(ns)

        for ns in dict.fromkeys([*model_namespaces, *chart_namespaces]):
            self._check_namespace(cmd, ns, errors)
            self._check_namespace_admin(cmd, ns, errors)
        for ns in chart_namespaces:
            self._check_chart_granted(cmd, ns, errors)

        if chart_namespaces:
            result = cmd.kube(
                "api-resources",
                f"--api-group={_REQUIRED_API_GROUP}",
                "-o",
                "name",
                check=False,
            )
            # discovery of an unrelated group can fail the whole command
            # while the answer is already on stdout
            if not result.stdout.strip():
                if result.success:
                    errors.append(
                        "the inferencepools CRD is not installed; ask an "
                        "admin to install it"
                    )
                else:
                    detail = f": {result.stderr}" if result.stderr else ""
                    errors.append(
                        f"could not ask the cluster which resources it serves{detail}"
                    )

        self._warn_on_elevated_security_context(context)

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Non-admin preflight failed",
                errors=errors,
            )

        ns_list = ", ".join(model_namespaces)
        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=f"Non-admin preflight passed (ns={ns_list})",
        )

    def _model_namespaces(self, context: ExecutionContext) -> list[str]:
        """Model namespaces only, without the harness namespace."""
        seen: list[str] = []
        for stack_path in context.rendered_stacks:
            cfg = self._load_stack_config(stack_path)
            ns = (cfg.get("namespace", {}) or {}).get("name")
            if ns and ns not in seen:
                seen.append(ns)
        if context.namespace and context.namespace not in seen:
            seen.append(context.namespace)
        return seen or [context.require_namespace()]

    @staticmethod
    def _can_i(cmd: CommandExecutor, verb: str, resource: str, namespace: str) -> str:
        """Return "yes" or "no"; can-i exits 1 on "no", so stdout decides."""
        result = cmd.kube(
            "auth",
            "can-i",
            verb,
            resource,
            namespace=namespace,
            check=False,
        )
        # the answer is the last stdout line; anything printed before it
        # must not turn a grant into an error
        lines = [line.strip().lower() for line in result.stdout.splitlines()]
        answer = lines[-1] if lines else ""
        if answer == "yes":
            return "yes"
        # a denial can carry a reason after the "no"
        if answer.startswith("no"):
            return "no"
        return "error"

    @staticmethod
    def _check_namespace(cmd: CommandExecutor, namespace: str, errors: list) -> None:
        result = cmd.kube("get", "namespace", namespace, check=False)
        if not result.success:
            errors.append(
                f"namespace {namespace} does not exist or is not visible; it "
                "must exist for a non-admin deploy"
            )

    def _check_namespace_admin(
        self, cmd: CommandExecutor, namespace: str, errors: list
    ) -> None:
        answer = self._can_i(cmd, "create", "rolebindings", namespace)
        if answer == "no":
            errors.append(
                f"user cannot create rolebindings in {namespace}; "
                "namespace-admin rights are required"
            )
        elif answer == "error":
            errors.append(
                f"could not ask the cluster about rolebinding rights in {namespace}"
            )

    def _check_chart_granted(
        self, cmd: CommandExecutor, namespace: str, errors: list
    ) -> None:
        denied = []
        errored = []
        for resource in _CHART_GRANTED_RESOURCES:
            # the chart role grants all three verbs; escalation prevention
            # demands the user holds each of them
            answers = {
                self._can_i(cmd, verb, resource, namespace)
                for verb in ("get", "list", "watch")
            }
            if "no" in answers:
                denied.append(resource)
            elif "error" in answers:
                errored.append(resource)
        if denied:
            errors.append(
                f"user cannot list {', '.join(denied)} in {namespace}; the "
                "router chart grants these to the EPP and only a holder may "
                "create that role -- ask an admin to grant them in the "
                "namespace"
            )
        if errored:
            errors.append(
                f"could not ask the cluster about {', '.join(errored)} in {namespace}"
            )

    def _warn_on_elevated_security_context(self, context: ExecutionContext) -> None:
        """An elevated security context needs SCCs only an admin can grant.

        Per stack, with the detection and the service-account resolution
        shared with step 08, so the two warnings cannot drift apart.
        """
        if not context.is_openshift:
            return
        # step 08, which would act on the grant, keys on deployed_methods
        if "modelservice" not in (context.deployed_methods or []):
            return
        for stack_path in context.rendered_stacks:
            cfg = self._load_stack_config(stack_path)
            if not cfg or not scenario_requests_elevation(cfg):
                continue
            if not (cfg.get("modelservice", {}) or {}).get("enabled", False):
                continue
            ns = (cfg.get("namespace", {}) or {}).get("name")
            if (cfg.get("gateway", {}) or {}).get("className") == "none":
                ns = resolve_direct_service_namespace(cfg, ns)
            sa = elevated_service_account(cfg)
            if not (ns and sa):
                continue
            context.logger.log_warning(
                f"scenario requests runAsUser/runAsGroup 0 for the {sa} "
                f"pods in {ns}; --non-admin skips the SCC grant, so pod "
                "creation is rejected unless a cluster-admin already "
                "granted it:\n"
                f"    oc adm policy add-scc-to-user privileged -z {sa} -n {ns}"
            )
