"""Step 02 -- Install cluster-level admin prerequisites (CRDs, gateways, LWS, SCCs)."""

from pathlib import Path

import yaml

from llmdbenchmark.executor.step import Step, StepResult, Phase
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.executor.command import CommandExecutor

GATEWAY_API_CRDS = [
    "gatewayclasses.gateway.networking.k8s.io",
    "gateways.gateway.networking.k8s.io",
    "grpcroutes.gateway.networking.k8s.io",
    "httproutes.gateway.networking.k8s.io",
    "referencegrants.gateway.networking.k8s.io",
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
    "inferencepools.inference.networking.x-k8s.io",
]

KGATEWAY_CRDS = [
    "backends.gateway.kgateway.dev",
    "directresponses.gateway.kgateway.dev",
    "gatewayextensions.gateway.kgateway.dev",
    "gatewayparameters.gateway.kgateway.dev",
    "httplistenerpolicies.gateway.kgateway.dev",
    "trafficpolicies.gateway.kgateway.dev",
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
                    f"📦 OCI registry detected for {repo_name} — no repo add required"
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
        crd_url = (
            f"github.com/kubernetes-sigs/gateway-api/"
            f"config/crd?ref={gw_revision}"
        )
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
        ext_url = (
            f"https://github.com/kubernetes-sigs/"
            f"gateway-api-inference-extension/"
            f"releases/download/{inf_ext_revision}/manifests.yaml"
        )
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

        if gateway_class == "kgateway":
            if not _any_crds_missing(KGATEWAY_CRDS, existing_crds):
                cmd.logger.log_info(
                    "✅ kgateway already installed "
                    "(*.gateway.kgateway.dev CRDs found)"
                )
                return
            self._install_kgateway(cmd, plan_config, errors)

        elif gateway_class == "istio":
            if not _any_crds_missing(ISTIO_CRDS, existing_crds):
                cmd.logger.log_info(
                    "✅ Istio already installed " "(*.istio.io CRDs found)"
                )
                return
            self._install_istio(cmd, context, plan_config, errors)

        elif gateway_class == "gke":
            cmd.logger.log_info("✅ GKE gateway is managed — nothing to install")

    def _install_lws_if_needed(
        self,
        cmd: CommandExecutor,
        plan_config: dict,
        errors: list,
        existing_crds: list[str],
    ):
        """Install LWS if configuration is present and CRDs are missing."""
        lws_config = plan_config.get("lws", {})
        if not lws_config:
            return

        if not _any_crds_missing(LWS_CRDS, existing_crds):
            cmd.logger.log_info(
                "✅ LeaderWorkerSet (LWS) controller already installed "
                "(leaderworkersets.leaderworkerset.x-k8s.io CRD found)"
            )
            return

        self._install_lws(cmd, lws_config, errors)

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
        """Apply OpenShift SCC assignments if on OpenShift."""
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

    def _install_kgateway(self, cmd: CommandExecutor, plan_config: dict, errors: list):
        kgw = plan_config.get("gatewayProviders", {}).get("kgateway", {})
        chart_version = kgw.get("chartVersion", "")
        namespace = self._require_config(kgw, "namespace")
        helm_repo = kgw.get("helmRepository", "")

        if not (helm_repo and chart_version):
            return

        def chart_ref(chart_name: str) -> str:
            if helm_repo.startswith("oci://"):
                return f"{helm_repo.rstrip('/')}/{chart_name}"
            return f"{helm_repo}/{chart_name}"

        cmd.logger.log_info(f"📦 Installing kgateway {chart_version}...")

        result = cmd.helm(
            "upgrade",
            "--install",
            "kgateway-crds",
            chart_ref("kgateway-crds"),
            "--version",
            chart_version,
            "--namespace",
            namespace,
            "--create-namespace",
            "--wait",
        )
        if not result.success:
            errors.append(f"Failed to install kgateway-crds: {result.stderr}")
            return

        result = cmd.helm(
            "upgrade",
            "--install",
            "kgateway",
            chart_ref("kgateway"),
            "--version",
            chart_version,
            "--namespace",
            namespace,
            "--create-namespace",
            "--set", "inferenceExtension.enabled=true",
            "--set", "controller.deployment.container.securityContext.seccompProfile.type=RuntimeDefault",
            "--set", "controller.deployment.container.securityContext.runAsNonRoot=true",
            "--set", "controller.deployment.container.securityContext.capabilities.drop={ALL}",
            "--wait",
        )

        if not result.success:
            errors.append(f"Failed to install kgateway: {result.stderr}")

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

        namespace = plan_config.get("namespace", {}).get("name", "")
        hf_args = []
        if namespace:
            hf_args.extend(["--namespace", namespace])
        hf_args.extend(
            [
                "apply",
                "-f",
                str(helmfile_yaml),
                "--skip-diff-on-install",
            ]
        )
        result = cmd.helmfile(*hf_args)
        if not result.success:
            errors.append(f"Failed to install Istio via helmfile: {result.stderr}")

    def _install_lws(self, cmd: CommandExecutor, lws_config: dict, errors: list):
        version = lws_config.get("chartVersion", "")
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
