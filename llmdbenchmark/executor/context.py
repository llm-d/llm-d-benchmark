"""Shared execution context carried through all pipeline phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from llmdbenchmark.executor.protocols import LoggerProtocol
from llmdbenchmark.executor.step import Phase

if TYPE_CHECKING:
    from llmdbenchmark.executor.command import CommandExecutor


@dataclass
class ExecutionContext:  # pylint: disable=too-many-instance-attributes
    """Shared state passed through all steps and phases, populated incrementally."""

    # Core paths
    plan_dir: Path
    workspace: Path
    base_dir: Path | None = None  # project root (for templates, scenarios, etc.)
    specification_file: str | None = None  # resolved --spec path
    rendered_stacks: list[Path] = field(default_factory=list)
    # Optional CLI filter: if set, per-stack steps only execute for stacks
    # whose name appears here. Useful in multi-stack scenarios to run
    # (or re-run) just one pool - e.g. `--stack pool-a` when benchmarking
    # a single model in the multi-model-wva scenario. Global steps are
    # unaffected. Empty / None means "all stacks" (existing behavior).
    stack_filter: list[str] | None = None

    # Execution flags
    dry_run: bool = False
    verbose: bool = False
    non_admin: bool = False
    current_phase: Phase = Phase.STANDUP
    deep_clean: bool = False  # teardown: wipe all resources in namespaces
    release: str = "llmdbench"  # Helm release name prefix

    # Kubernetes connection info (resolved at runtime by step 00)
    cluster_url: str | None = None
    cluster_token: str | None = None
    kubeconfig: str | None = None

    # Platform detection flags (set by step 00)
    is_openshift: bool = False
    is_gke: bool = False
    is_kind: bool = False
    is_minikube: bool = False
    # Resolved cluster metadata
    cluster_name: str | None = None  # hostname from API server URL
    cluster_server: str | None = None  # full API server URL
    context_name: str | None = None  # kube context name
    username: str | None = None  # current user for labeling

    # Namespace info (populated from plan config / CLI overrides)
    namespace: str | None = None
    harness_namespace: str | None = None
    wva_namespace: str | None = None

    # OpenShift UID range -- (first_uid + 1) from openshift.io/sa.scc.uid-range
    proxy_uid: int | None = None

    # Model info (populated from plan config)
    model_name: str | None = None  # e.g. "meta-llama/Llama-3.1-8B"

    # Deployed state (populated during standup, consumed by run)
    deployed_endpoints: dict[str, str] = field(default_factory=dict)
    deployed_methods: list[str] = field(default_factory=list)

    # Node resource discovery (populated during step 03)
    accelerator_resource: str | None = None  # e.g. "nvidia.com/gpu"
    network_resource: str | None = None  # e.g. "rdma/rdma_shared_device_a"

    # Experiment state (populated during run)
    experiment_treatments: list[dict] | None = None
    results_dir: Path | None = None

    # Harness pods deployed in this run (step_06 writes, step_07/08/10 reads)
    deployed_pod_names: list[str] = field(default_factory=list)

    # Experiment IDs generated in this run (step_06 writes, step_08 reads)
    experiment_ids: list[str] = field(default_factory=list)

    # Run-phase configuration (set by _execute_run)
    harness_name: str | None = None
    harness_profile: str | None = None
    experiment_treatments_file: str | None = None
    profile_overrides: str | None = None
    harness_output: str = "local"
    harness_parallelism: int = 1
    harness_wait_timeout: int = 3600
    harness_debug: bool = False
    harness_skip_run: bool = False
    harness_service_account: str | None = None
    harness_envvars_to_pod: str | None = None
    analyze_locally: bool = False
    harness_data_access_timeout: int = 120

    # Standup pod deployment timeouts
    standalone_deploy_timeout: int = 900
    gateway_deploy_timeout: int = 120
    modelservice_deploy_timeout: int = 1500

    pvc_bind_timeout: int = 240

    # Teardown timeouts
    fma_teardown_timeout: int = 120

    # Run-only mode (existing-stack)
    endpoint_url: str | None = None
    run_config_file: str | None = None
    generate_config_only: bool = False
    dataset_url: str | None = None

    logger: LoggerProtocol | None = field(default=None, repr=False)

    # Call rebuild_cmd() after changing kubeconfig or is_openshift.
    cmd: CommandExecutor | None = field(default=None, repr=False)

    _cluster_resolved: bool = field(default=False, repr=False)

    # Command paths (auto-detected)
    kubectl_cmd: str = "kubectl"
    helm_cmd: str = "helm"
    helmfile_cmd: str = "helmfile"
    python_cmd: str = "python3"

    def rebuild_cmd(self) -> CommandExecutor:
        """Create or recreate the shared CommandExecutor from current context fields."""
        from llmdbenchmark.executor.command import CommandExecutor as _CE

        self.cmd = _CE(
            work_dir=self.workspace,
            dry_run=self.dry_run,
            verbose=self.verbose,
            logger=self.logger,
            kubeconfig=self.kubeconfig,
            kube_context=self.context_name,
            openshift=self.is_openshift,
        )
        return self.cmd

    def resolve_cluster(self) -> None:
        """Resolve cluster connectivity and metadata (idempotent)."""
        if self._cluster_resolved:
            return
        from llmdbenchmark.utilities.cluster import resolve_cluster as _resolve

        _resolve(self)
        self._cluster_resolved = True

    def require_cmd(self) -> CommandExecutor:
        """Return the shared CommandExecutor, raising if not yet initialized."""
        if self.cmd is None:
            raise RuntimeError(
                "CommandExecutor not initialised. "
                "Call context.rebuild_cmd() or run step 00 first."
            )
        return self.cmd

    def require_namespace(self) -> str:
        """Return the namespace, raising if not configured."""
        if not self.namespace:
            raise RuntimeError(
                "No namespace configured. Set 'namespace.name' in your "
                "scenario YAML, defaults.yaml, or pass via the CLI."
            )
        return self.namespace

    @property
    def platform_type(self) -> str:
        """Human-readable platform label (e.g. 'OpenShift', 'Kind')."""
        if self.is_openshift:
            return "OpenShift"
        if self.is_gke:
            return "GKE"
        if self.is_kind:
            return "Kind"
        if self.is_minikube:
            return "Minikube"
        return "Kubernetes"

    def setup_commands_dir(self) -> Path:
        """Path to workspace/setup/commands, created on access."""
        commands_dir = self.workspace / "setup" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        return commands_dir

    def setup_yamls_dir(self) -> Path:
        """Path to workspace/setup/yamls, created on access."""
        yamls_dir = self.workspace / "setup" / "yamls"
        yamls_dir.mkdir(parents=True, exist_ok=True)
        return yamls_dir

    def setup_logs_dir(self) -> Path:
        """Path to workspace/setup/logs, created on access."""
        logs_dir = self.workspace / "setup" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def setup_helm_dir(self) -> Path:
        """Path to workspace/setup/helm, created on access."""
        helm_dir = self.workspace / "setup" / "helm"
        helm_dir.mkdir(parents=True, exist_ok=True)
        return helm_dir

    def environment_dir(self) -> Path:
        """Path to workspace/environment, created on access."""
        env_dir = self.workspace / "environment"
        env_dir.mkdir(parents=True, exist_ok=True)
        return env_dir

    def run_dir(self) -> Path:
        """Path to workspace/run, created on access."""
        d = self.workspace / "run"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run_results_dir(self) -> Path:
        """Path to workspace/results, created on access."""
        d = self.workspace / "results"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def run_analysis_dir(self) -> Path:
        """Path to workspace/analysis, created on access."""
        d = self.workspace / "analysis"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def workload_profiles_dir(self) -> Path:
        """Path to workspace/workload/profiles, created on access."""
        d = self.workspace / "workload" / "profiles"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def is_run_only_mode(self) -> bool:
        """True when running against an existing stack (run-only mode)."""
        return bool(self.endpoint_url or self.run_config_file)

    def preprocess_dir(self) -> Path | None:
        """Locate the preprocess scripts directory (package-relative, then base_dir fallback)."""
        pkg_dir = Path(__file__).resolve().parent.parent  # llmdbenchmark/
        d = pkg_dir / "standup" / "preprocess"
        if d.is_dir():
            return d
        if self.base_dir:
            d = self.base_dir / "llmdbenchmark" / "standup" / "preprocess"
            if d.is_dir():
                return d
        return None
