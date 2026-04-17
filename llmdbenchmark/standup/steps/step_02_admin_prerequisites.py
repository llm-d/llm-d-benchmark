"""Step 02 -- Install cluster-level admin prerequisites (CRDs, gateways, LWS, SCCs)."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor

# Name of the custom OpenShift SCC for the agentgateway data-plane proxy.
# The SCC definition lives in config/templates/jinja/05a_agentgateway_scc.yaml.j2
# and is rendered at plan time.
_AGENTGATEWAY_SCC_NAME = "llmdbench-agentgateway"

GATEWAY_API_CRDS = [
    "backendtlspolicies.gateway.networking.k8s.io",
    "gatewayclasses.gateway.networking.k8s.io",
    "gateways.gateway.networking.k8s.io",
    "grpcroutes.gateway.networking.k8s.io",
    "httproutes.gateway.networking.k8s.io",
    "listenersets.gateway.networking.k8s.io",
    "referencegrants.gateway.networking.k8s.io",
    "tlsroutes.gateway.networking.k8s.io"
]

# Inference extension CRDs may use the graduated (.k8s.io) or
# experimental (.x-k8s.io) API group depending on the installed version.
# We check for both variants.
GATEWAY_API_EXTENSION_CRDS_K8S = [
    "inferencemodelrewrites.inference.networking.k8s.io",
    "inferenceobjectives.inference.networking.k8s.io",
    "inferencepoolimports.inference.networking.k8s.io",
    "inferencepools.inference.networking.k8s.io",
]
GATEWAY_API_EXTENSION_CRDS_XK8S = [
    "inferencemodelrewrites.inference.networking.x-k8s.io",
    "inferenceobjectives.inference.networking.x-k8s.io",
    "inferencepoolimports.inference.networking.x-k8s.io",
    "inferencepools.inference.networking.x-k8s.io"
]

AGENTGATEWAY_CRDS = [
    "agentgatewaybackends.agentgateway.dev",
    "agentgatewayparameters.agentgateway.dev",
    "agentgatewaypolicies.agentgateway.dev"
]

ISTIO_CRDS = [
    "authorizationpolicies.security.istio.io",
    "destinationrules.networking.istio.io",
    "envoyfilters.networking.istio.io",
    "gateways.networking.istio.io",
    "peerauthentications.security.istio.io",
    "proxyconfigs.networking.istio.io",
    "requestauthentications.security.istio.io",
    "sidecars.networking.istio.io",
    "telemetries.telemetry.istio.io",
    "virtualservices.networking.istio.io",
    "wasmplugins.extensions.istio.io",
    "workloadgroups.networking.istio.io",
]

LWS_CRDS = [
    "leaderworkersets.leaderworkerset.x-k8s.io",
]


def _any_crds_missing(expected: list[str], existing: list[str]) -> bool:
    """Return True if any of the expected CRDs are absent from the cluster."""
    return not set(expected).issubset(existing)


class AdminPrerequisitesStep(Step):
    """Install cluster-level admin prerequisites such as CRDs and gateways."""

    def __init__(self):
        super().__init__(
            number=2,
            name="admin_prerequisites",
            description="Install cluster-level admin prerequisites",
            phase=Phase.STANDUP,
            per_stack=False,
        )

    def should_skip(self, context: ExecutionContext) -> bool:
        return context.non_admin

    def execute(
        self, context: ExecutionContext, stack_path: Path | None = None
    ) -> StepResult:
        errors = []
        cmd = context.require_cmd()

        plan_config = self._load_plan_config(context)
        if plan_config is None:
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Could not load plan configuration",
                errors=["No rendered stack configuration found"],
            )

        self._add_helm_repos(cmd, plan_config, errors)

        existing_crds = self._get_existing_crds(cmd, context)

        self._install_gateway_api_crds(
            cmd,
            plan_config,
            errors,
            existing_crds,
        )

        deploy_methods = context.deployed_methods or []
        modelservice_active = "modelservice" in deploy_methods

        if modelservice_active:
            self._install_gateway_api_extension_crds(
                cmd,
                plan_config,
                errors,
                existing_crds,
            )
            self._install_gateway_provider(
                cmd,
                context,
                plan_config,
                errors,
                existing_crds,
            )
            self._install_lws_if_needed(
                cmd,
                plan_config,
                errors,
                existing_crds,
            )

            self._install_prometheus_crds_if_needed(
                cmd, plan_config, existing_crds,
            )

        # Also install Prometheus CRDs for standalone (outside modelservice block)
        if not modelservice_active:
            self._install_prometheus_crds_if_needed(
                cmd, plan_config, existing_crds,
            )

        self._apply_namespace_yaml(cmd, context, errors)
        self._apply_openshift_sccs(cmd, context, plan_config)

        if errors:
            for err in errors:
                context.logger.log_error(f"    {err}")
            return StepResult(
                step_number=self.number,
                step_name=self.name,
                success=False,
                message="Some admin prerequisites failed",
                errors=errors,
            )

        return StepResult(
            step_number=self.number,
            step_name=self.name,
            success=True,
            message="Admin prerequisites installed",
        )

    def _get_existing_crds(
        self, cmd: CommandExecutor, context: ExecutionContext
    ) -> list[str]:
        """Fetch all CRD names currently registered in the cluster."""
        if context.dry_run:
            return []

        result = cmd.kube(
            "get",
            "crd",
            "-o",
            "jsonpath={.items[*].metadata.name}",
        )
        if result.success and result.stdout.strip():
            return result.stdout.strip().split()
        return []

    def _add_helm_repos(self, cmd: CommandExecutor, plan_config: dict, errors: list):
        """Add configured Helm repositories."""
        helm_repos = plan_config.get("helmRepositories", {})
        added_classic_repo = False

        for repo_key, repo_info in helm_repos.items():
            repo_name = repo_info.get("name", repo_key)
            repo_url = repo_info.get("url", "").strip()
            if not repo_url:
                continue

            if repo_url.startswith("oci://"):
                cmd.logger.log_info(
                    f"📦 OCI registry detected for {repo_name} -- no repo add required"
                )
                continue

            result = cmd.helm("repo", "add", repo_name, repo_url, "--force-update")
            if not result.success:
                errors.append(f"Failed to add helm repo {repo_name}: {result.stderr}")
            else:
                added_classic_repo = True

        if added_classic_repo:
            cmd.helm("repo", "update")

    def _install_gateway_api_crds(
        self,
        cmd: CommandExecutor,
        plan_config: dict,
        errors: list,
        existing_crds: list[str],
    ):
        """Install Gateway API CRDs if any are missing."""
        gw_api = plan_config.get("gatewayApiCrd", {})
        gw_revision = gw_api.get("revision", "")
        if not gw_revision:
            return

        if not _any_crds_missing(GATEWAY_API_CRDS, existing_crds):
            cmd.logger.log_info(
                "✅ Gateway API CRDs already installed "
                "(*.gateway.networking.k8s.io found)"
            )
            return

        cmd.logger.log_info(
            f"📦 Installing Gateway API CRDs (revision {gw_revision})..."
        )
        # URL template lives in defaults.yaml so it has a single source of
        # truth (gatewayApiCrd.crdUrlTemplate). Fail loudly if missing -- we
        # don't want a stale hardcoded URL silently substituting in.
        crd_url_template = self._require_config(
            plan_config, "gatewayApiCrd", "crdUrlTemplate",
        )
        crd_url = crd_url_template.format(revision=gw_revision)
        result = cmd.kube("apply", "--server-side", "-k", crd_url)
        if not result.success:
            errors.append(f"Failed to install Gateway API CRDs: {result.stderr}")

    def _install_gateway_api_extension_crds(
        self,
        cmd: CommandExecutor,
        plan_config: dict,
        errors: list,
        existing_crds: list[str],
    ):
        """Install inference extension CRDs if any are missing."""
        gw_api = plan_config.get("gatewayApiCrd", {})
        inf_ext_revision = gw_api.get("inferenceExtensionRevision", "")
        if not inf_ext_revision:
            return

        # Accept either .k8s.io (graduated) or .x-k8s.io (experimental)
        k8s_present = not _any_crds_missing(
            GATEWAY_API_EXTENSION_CRDS_K8S, existing_crds
        )
        xk8s_present = not _any_crds_missing(
            GATEWAY_API_EXTENSION_CRDS_XK8S, existing_crds
        )
        if k8s_present or xk8s_present:
            variant = ".k8s.io" if k8s_present else ".x-k8s.io"
            cmd.logger.log_info(
                f"✅ Gateway API inference extension CRDs already installed "
                f"(*.inference.networking{variant} found)"
            )
            return

        cmd.logger.log_info(
            f"📦 Installing inference extension CRDs "
            f"(revision {inf_ext_revision})..."
        )
        # URL template lives in defaults.yaml so it has a single source of
        # truth (gatewayApiCrd.inferenceExtensionUrlTemplate). Fail loudly if
        # missing -- silent fallback would hide config drift.
        ext_url_template = self._require_config(
            plan_config, "gatewayApiCrd", "inferenceExtensionUrlTemplate",
        )
        ext_url = ext_url_template.format(revision=inf_ext_revision)
        result = cmd.kube("apply", "-f", ext_url)
        if not result.success:
            errors.append(
                f"Failed to install inference extension CRDs: " f"{result.stderr}"
            )

    def _install_gateway_provider(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        errors: list,
        existing_crds: list[str],
    ):
        """Install the gateway provider only if its CRDs are missing."""
        gateway_config = plan_config.get("gateway", {})
        gateway_class = self._require_config(plan_config, "gateway", "className")

        if gateway_class == "agentgateway":
            if not _any_crds_missing(AGENTGATEWAY_CRDS, existing_crds):
                cmd.logger.log_info(
                    "✅ agentgateway already installed "
                    "(*.agentgateway.dev CRDs found)"
                )
                return
            self._install_agentgateway(cmd, context, errors)

        elif gateway_class == "istio":
            if not _any_crds_missing(ISTIO_CRDS, existing_crds):
                cmd.logger.log_info(
                    "✅ Istio already installed " "(*.istio.io CRDs found)"
                )
                return
            self._install_istio(cmd, context, plan_config, errors)

        elif gateway_class == "gke":
            cmd.logger.log_info("✅ GKE gateway is managed -- nothing to install")

    def _install_lws_if_needed(
        self,
        cmd: CommandExecutor,
        plan_config: dict,
        errors: list,
        existing_crds: list[str],
    ):
        """Install LWS only when multinode is enabled and CRDs are missing.

        The bash implementation only installed LWS when
        LLMDBENCH_VLLM_MODELSERVICE_MULTINODE was true (e.g., wide-ep-lws).
        """
        multinode = plan_config.get("multinode", {})
        if not multinode.get("enabled", False):
            return

        lws_config = plan_config.get("lws", {})
        if not lws_config:
            return

        if not _any_crds_missing(LWS_CRDS, existing_crds):
            cmd.logger.log_info(
                "✅ LeaderWorkerSet (LWS) controller already installed "
                "(leaderworkersets.leaderworkerset.x-k8s.io CRD found)"
            )
            return

        self._install_lws(cmd, lws_config, errors, plan_config=plan_config)

    def _install_prometheus_crds_if_needed(
        self,
        cmd: CommandExecutor,
        plan_config: dict,
        existing_crds: list[str],
    ):
        """Install Prometheus Operator CRDs (PodMonitor, ServiceMonitor) if requested.

        Only installs when monitoring.installPrometheusCrds is true and the
        CRDs don't already exist. Useful for Kind or vanilla K8s clusters
        that don't have the Prometheus Operator installed.
        """
        monitoring = plan_config.get("monitoring", {})
        if not monitoring.get("installPrometheusCrds", False):
            return

        prometheus_crds = [
            "podmonitors.monitoring.coreos.com",
            "servicemonitors.monitoring.coreos.com",
        ]

        if not _any_crds_missing(prometheus_crds, existing_crds):
            cmd.logger.log_info(
                "✅ Prometheus Operator CRDs already installed "
                "(podmonitors.monitoring.coreos.com found)"
            )
            return

        cmd.logger.log_info(
            "Installing Prometheus Operator CRDs (PodMonitor, ServiceMonitor)..."
        )
        urls = monitoring.get("prometheusCrdUrls", [])
        if not urls:
            cmd.logger.log_warning(
                "monitoring.prometheusCrdUrls is empty -- cannot install CRDs"
            )
            return
        for url in urls:
            result = cmd.kube("apply", "-f", url, check=False)
            if not result.success:
                cmd.logger.log_warning(
                    f"Failed to install Prometheus CRD from {url}: {result.stderr}"
                )
                return

        cmd.logger.log_info(
            "✅ Prometheus Operator CRDs installed (PodMonitor, ServiceMonitor)"
        )

    def _apply_namespace_yaml(
        self, cmd: CommandExecutor, context: ExecutionContext, errors: list
    ):
        """Create namespaces from rendered YAML."""
        ns_yaml = self._find_rendered_yaml(context, "05_namespace_sa_rbac_secret")
        if ns_yaml:
            result = cmd.kube("apply", "-f", str(ns_yaml))
            if not result.success:
                errors.append(f"Failed to create namespace resources: {result.stderr}")

    def _apply_openshift_sccs(
        self, cmd: CommandExecutor, context: ExecutionContext, plan_config: dict
    ):
        """Apply OpenShift SCC assignments if on OpenShift.

        Grants ``anyuid`` and ``privileged`` SCCs to the vLLM workload
        service account.  When the gateway provider is **agentgateway**,
        creates a minimal custom SCC (``llmdbench-agentgateway``) that
        permits only UID 10101 and the ``NET_BIND_SERVICE`` capability,
        then binds it to the gateway proxy service account
        (``infra-{release}-inference-gateway``).
        """
        if context.is_openshift:
            namespace = plan_config.get("namespace", {}).get("name", "")
            if namespace:
                service_account = self._require_config(plan_config, "serviceAccount", "name")
                for scc in ["anyuid", "privileged"]:
                    cmd.kube(
                        "adm",
                        "policy",
                        "add-scc-to-user",
                        scc,
                        "-z",
                        service_account,
                        "-n",
                        namespace,
                    )

                # agentgateway proxy pods run as UID 10101 and add the
                # NET_BIND_SERVICE capability.  Instead of granting the
                # overly broad "privileged" SCC, we create a minimal
                # custom SCC that only permits what the proxy needs and
                # bind it to the gateway service account.
                gateway_class = plan_config.get("gateway", {}).get("className", "")
                if gateway_class == "agentgateway":
                    self._ensure_agentgateway_scc(cmd, context, namespace, plan_config)

    def _ensure_agentgateway_scc(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        namespace: str,
        plan_config: dict,
    ):
        """Apply the custom agentgateway SCC and bind it to the gateway SA.

        The SCC definition is rendered from
        ``config/templates/jinja/05a_agentgateway_scc.yaml.j2`` at plan
        time.  This method applies it (cluster-scoped, idempotent) and
        grants it to the gateway service account in the target namespace.
        """
        release = plan_config.get("release", "llmdbench")
        gw_sa = f"infra-{release}-inference-gateway"

        # Apply the rendered SCC template.
        scc_yaml = self._find_rendered_yaml(context, "05a_agentgateway_scc")
        if not scc_yaml or not self._has_yaml_content(scc_yaml):
            cmd.logger.log_info(
                "    No agentgateway SCC template rendered -- skipping"
            )
            return

        result = cmd.kube("apply", "-f", str(scc_yaml))
        if not result.success:
            cmd.logger.log_warning(
                f"    Failed to apply SCC '{_AGENTGATEWAY_SCC_NAME}': "
                f"{result.stderr}"
            )
            return

        cmd.logger.log_info(
            f"    ✅ SCC '{_AGENTGATEWAY_SCC_NAME}' applied"
        )

        # Bind the SCC to the gateway service account.
        cmd.logger.log_info(
            f"    Granting '{_AGENTGATEWAY_SCC_NAME}' SCC to gateway SA "
            f"'{gw_sa}' in namespace '{namespace}'"
        )
        cmd.kube(
            "adm",
            "policy",
            "add-scc-to-user",
            _AGENTGATEWAY_SCC_NAME,
            "-z",
            gw_sa,
            "-n",
            namespace,
        )

    def _install_agentgateway(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        errors: list,
    ):
        """Install agentgateway CRDs + controller via the rendered helmfile.

        The helmfile itself is rendered by
        ``config/templates/jinja/09_helmfile-gateway-provider.yaml.j2``
        during the ``plan`` phase -- we just locate the rendered file
        and hand it to ``helmfile apply``. This is the same pattern
        ``_install_istio`` uses, and it keeps all YAML assembly in the
        templates rather than in Python string-concatenation here.

        The canonical upstream helmfile this mirrors is:
          https://raw.githubusercontent.com/llm-d-incubation/llm-d-infra/refs/heads/main/quickstart/gateway-control-plane-providers/kgateway.helmfile.yaml

        We deliberately pass ``use_kubeconfig=False`` for the same
        reason ``_install_istio`` does: helmfile must resolve release
        namespaces from the helmfile itself (``kgateway-system``), not
        from whatever namespace context the kubeconfig carries, or the
        ``needs:`` wiring between the CRDs release and the controller
        release will not resolve correctly.
        """
        helmfile_yaml = self._find_rendered_yaml(
            context, "09_helmfile-gateway-provider"
        )
        if not helmfile_yaml or not self._has_yaml_content(helmfile_yaml):
            return

        cmd.logger.log_info("📦 Installing agentgateway via helmfile...")

        result = cmd.helmfile(
            "apply",
            "-f",
            str(helmfile_yaml),
            "--skip-diff-on-install",
            use_kubeconfig=False,
        )
        if not result.success:
            errors.append(f"Failed to install agentgateway via helmfile: {result.stderr}")

    def _install_istio(
        self,
        cmd: CommandExecutor,
        context: ExecutionContext,
        plan_config: dict,
        errors: list,
    ):
        """Install Istio via helmfile if a rendered helmfile is available."""
        helmfile_yaml = self._find_rendered_yaml(
            context, "09_helmfile-gateway-provider"
        )
        if not helmfile_yaml:
            return

        cmd.logger.log_info("📦 Installing Istio via helmfile...")

        # Match bash behavior: call helmfile WITHOUT --kubeconfig and
        # WITHOUT --namespace so helmfile resolves release namespaces
        # from the helmfile itself (istio-system), not from the
        # kubeconfig context namespace (e.g., llmdbenchcicd).
        result = cmd.helmfile(
            "apply",
            "-f",
            str(helmfile_yaml),
            "--skip-diff-on-install",
            use_kubeconfig=False,
        )
        if not result.success:
            errors.append(f"Failed to install Istio via helmfile: {result.stderr}")

    def _install_lws(self, cmd: CommandExecutor, lws_config: dict, errors: list, plan_config: dict | None = None):
        version = ""
        if plan_config:
            version = plan_config.get("chartVersions", {}).get("lws", "")
        version = version or lws_config.get("chartVersion", "")
        namespace = self._require_config(lws_config, "namespace")
        helm_repo = lws_config.get("helmRepository", "")

        if not (version and helm_repo):
            return

        def chart_ref() -> str:
            if helm_repo.startswith("oci://"):
                return f"{helm_repo.rstrip('/')}/lws"
            return f"{helm_repo}/lws"

        cmd.logger.log_info(f"📦 Installing LeaderWorkerSet (LWS) v{version}...")

        result = cmd.helm(
            "upgrade",
            "--install",
            "lws",
            chart_ref(),
            "--version",
            version,
            "--namespace",
            namespace,
            "--create-namespace",
            "--wait",
            "--timeout",
            "300s",
        )

        if not result.success:
            errors.append(f"Failed to install LWS: {result.stderr}")

    def _load_plan_config(self, context: ExecutionContext) -> dict | None:
        """Load config from the first rendered stack, falling back to plan_dir."""
        config = super()._load_plan_config(context)
        if config is not None:
            return config
        plan_dir = context.plan_dir
        if plan_dir:
            config_file = plan_dir / "config.yaml"
            if config_file.exists():
                with open(config_file, encoding="utf-8") as f:
                    return yaml.safe_load(f)
        return {}
