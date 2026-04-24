"""Step 07 -- Set up Helm repos and deploy gateway infrastructure for modelservice."""

import shutil
from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext


class DeploySetupStep(Step):
    """Set up Helm repositories and deploy gateway infrastructure."""

    # Map from rendered template prefix to the filename helmfile expects.
    # Helmfile references these by relative path in its values: sections.
    _VALUES_FILE_MAP = {
        "11_infra": "infra.yaml",
        "12_gaie-values": "gaie-values.yaml",
        "13_ms-values": "ms-values.yaml",
    }

    def __init__(self):
        super().__init__(
            number=7,
            name="deploy_setup",
            description="Set up Helm repos and gateway infrastructure",
            phase=Phase.STANDUP,
            per_stack=True,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return "modelservice" not in context.deployed_methods

    def execute(
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
        release = self._require_config(plan_config, "release")
        namespace = context.require_namespace()

        helm_dir = self._prepare_helm_dir(context, stack_path, errors)

        gateway_class = self._require_config(plan_config, "gateway", "className")
        if context.is_openshift and gateway_class == "agentgateway" and helm_dir:
            self._patch_infra_for_openshift_agentgateway(helm_dir, context)

        # Gateway provider helmfile (Istio) -- matches bash behavior:
        # call helmfile WITHOUT --kubeconfig so it uses the default context.
        # This ensures helmfile resolves release namespaces (istio-system)
        # from the helmfile itself, not from the kubeconfig context namespace
        # which may be set to the benchmark namespace (e.g., llmdbenchcicd).
        gw_helmfile = self._find_yaml(stack_path, "09_helmfile-gateway-provider")
        if gw_helmfile and self._has_yaml_content(gw_helmfile):
            result = cmd.helmfile(
                "apply",
                "-f",
                str(gw_helmfile),
                "--skip-diff-on-install",
                "--skip-schema-validation",
                use_kubeconfig=False,
            )
            if not result.success:
                errors.append(
                    f"Failed to install Istio via helmfile: {result.stderr}"
                )

        # Helmfile is copied to helm working dir so relative value paths resolve
        main_helmfile = self._find_yaml(stack_path, "10_helmfile-main")
        if main_helmfile and helm_dir:
            helmfile_work = helm_dir / "helmfile.yaml"
            shutil.copy2(main_helmfile, helmfile_work)

            if context.non_admin:
                self._patch_helmfile_for_non_admin(helmfile_work)

            # Multi-stack scenarios dedupe the `infra-{release}` helm
            # release into one stack (see 10_helmfile-main.yaml.j2 —
            # stacks 2..N omit it). Pre-check whether THIS stack's
            # helmfile declares the release before invoking helmfile;
            # otherwise the apply exits non-zero with "no releases found
            # that matches specified selector" which is harmless but
            # noisy in the standup log.
            if self._helmfile_declares_release(helmfile_work, f"infra-{release}"):
                result = cmd.helmfile(
                    "--namespace",
                    namespace,
                    "--selector",
                    f"name=infra-{release}",
                    "apply",
                    "-f",
                    str(helmfile_work),
                    "--skip-diff-on-install",
                    "--skip-schema-validation",
                )
                if not result.success:
                    errors.append(f"Failed to apply infra helmfile: {result.stderr}")
            else:
                context.logger.log_info(
                    f"    │ infra-{release} not in this stack's helmfile "
                    f"(shared infra is owned by another stack) — skipping"
                )

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Deploy setup had errors",
                errors=errors,
                stack_name=stack_path.name,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message=(
                "Helm repos and gateway infrastructure deployed "
                f"for {stack_path.name}"
            ),
            stack_name=stack_path.name,
        )

    @staticmethod
    def _helmfile_declares_release(helmfile_path: Path, release_name: str) -> bool:
        """Return True if *helmfile_path* declares a release named *release_name*.

        Used to skip a ``helmfile apply --selector`` invocation that would
        otherwise "fail" noisily on stacks that intentionally dropped a
        shared release (e.g. ``infra-llmdbench`` in multi-stack scenarios).

        Parses the helmfile YAML and walks ``releases[*].name`` — a
        substring check was too permissive: a label value, annotation,
        or values-block key with the text ``name: X`` would falsely
        match without actually declaring a release.
        """
        try:
            content = helmfile_path.read_text(encoding="utf-8")
            docs = list(yaml.safe_load_all(content))
        except (OSError, yaml.YAMLError):
            return False
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            for release in doc.get("releases") or []:
                if isinstance(release, dict) and release.get("name") == release_name:
                    return True
        return False

    def _prepare_helm_dir(
        self, context: ExecutionContext, stack_path: Path, errors: list
    ) -> Path | None:
        """Prepare a helm working directory with value files named as helmfile expects."""
        try:
            helm_dir = context.setup_helm_dir() / stack_path.name
            helm_dir.mkdir(parents=True, exist_ok=True)

            for prefix, target_name in self._VALUES_FILE_MAP.items():
                source = self._find_yaml(stack_path, prefix)
                if source:
                    shutil.copy2(source, helm_dir / target_name)

            return helm_dir
        except OSError as exc:
            errors.append(f"Failed to prepare helm directory: {exc}")
            return None

    def _patch_infra_for_openshift_agentgateway(
        self, helm_dir: Path, context: ExecutionContext
    ):
        """Patch infra.yaml for agentgateway on OpenShift.

        Unlike Istio, agentgateway does NOT use ConfigMap-based
        ``gatewayParameters``.  Setting ``gatewayParameters.enabled: true``
        causes the llm-d-infra chart to create a ``parametersRef`` of
        ``kind: ConfigMap`` on the Gateway, which agentgateway rejects:

            references unsupported type: group= kind=ConfigMap;
            use AgentgatewayParameters instead

        The agentgateway controller manages its own security context
        through the helm values installed via the helmfile
        (``securityContext.runAsNonRoot``, ``allowPrivilegeEscalation: false``),
        so we do NOT need to inject ``floatingUserId`` here.

        This method is intentionally a no-op.  It is kept as a placeholder
        in case agentgateway-specific OpenShift patches are needed in the
        future (e.g., creating an ``AgentgatewayParameters`` CR).
        """
        context.logger.log_info(
            "agentgateway on OpenShift: no infra.yaml patch needed "
            "(controller handles securityContext via helm values)"
        )

    def _patch_helmfile_for_non_admin(self, helmfile_path: Path):
        """Prepend ``helmDefaults: createNamespace: false`` for non-admin users."""
        try:
            content = helmfile_path.read_text(encoding="utf-8")
            if "helmDefaults:" not in content:
                patched = (
                    "helmDefaults:\n" "  createNamespace: false\n" "---\n" + content
                )
                helmfile_path.write_text(patched, encoding="utf-8")
        except OSError:
            pass
