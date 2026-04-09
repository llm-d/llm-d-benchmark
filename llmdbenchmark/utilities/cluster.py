"""Cluster resolution, Kubernetes connectivity, and platform detection."""

from __future__ import annotations

import getpass
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import yaml

if TYPE_CHECKING:
    from llmdbenchmark.executor.command import CommandExecutor
    from llmdbenchmark.executor.context import ExecutionContext

try:
    from kubernetes import client, config as k8s_config
    from kubernetes.client.rest import ApiException

    _KUBE_AVAILABLE = True
except ImportError:
    _KUBE_AVAILABLE = False

try:
    import urllib3
    from urllib3.exceptions import InsecureRequestWarning

    _URLLIB3_AVAILABLE = True
except ImportError:
    _URLLIB3_AVAILABLE = False


def resolve_cluster(context: ExecutionContext) -> None:
    """Connect to the cluster, detect platform, store kubeconfig, and resolve metadata.

    Runs fully even in dry-run mode so that the stored ``context.ctx``
    kubeconfig is created and all subsequent dry-run commands show the
    canonical kubeconfig path.
    """
    _kube_api_connect(context)
    cmd = context.rebuild_cmd()
    _detect_local_platform(cmd, context)
    _store_kubeconfig(cmd, context)
    cmd = context.rebuild_cmd()
    _resolve_cluster_metadata(cmd, context)


def _silence_insecure_tls_warnings_if_disabled() -> None:
    """Suppress urllib3 InsecureRequestWarning when the active Kubernetes
    configuration has TLS verification disabled.

    Users who set ``insecure-skip-tls-verify: true`` in their kubeconfig
    (or who connect via ``cluster_url`` + ``token`` below) have explicitly
    opted into insecure TLS, so emitting one warning per API call is pure
    noise. We only silence the warning when ``verify_ssl`` is actually
    ``False`` so normal (verified) connections still surface any real
    insecure-request warnings from other code paths (e.g. HuggingFace
    access checks).
    """
    if not _URLLIB3_AVAILABLE:
        return

    try:
        active = client.Configuration.get_default_copy()
    except Exception:  # pylint: disable=broad-except
        return

    if getattr(active, "verify_ssl", True) is False:
        urllib3.disable_warnings(InsecureRequestWarning)


def kube_connect(
    kubeconfig: str | None = None,
    kube_context: str | None = None,
    cluster_url: str | None = None,
    token: str | None = None,
) -> client.ApiClient:
    """Establish a Kubernetes API connection using kubeconfig, token, or in-cluster config."""
    if not _KUBE_AVAILABLE:
        raise ImportError(
            "Kubernetes Python client is not installed. "
            "Install with: pip install kubernetes"
        )

    if kubeconfig:
        k8s_config.load_kube_config(
            config_file=kubeconfig, context=kube_context,
        )
        _silence_insecure_tls_warnings_if_disabled()
        return client.ApiClient()

    if cluster_url and token:
        configuration = client.Configuration()
        configuration.host = cluster_url
        configuration.api_key = {"authorization": f"Bearer {token}"}
        configuration.verify_ssl = False
        _silence_insecure_tls_warnings_if_disabled()
        return client.ApiClient(configuration)

    try:
        k8s_config.load_kube_config(context=kube_context)
        _silence_insecure_tls_warnings_if_disabled()
        return client.ApiClient()
    except k8s_config.ConfigException:
        k8s_config.load_incluster_config()
        _silence_insecure_tls_warnings_if_disabled()
        return client.ApiClient()


def is_openshift(api_client: client.ApiClient) -> bool:
    """Return True if the cluster is an OpenShift cluster.

    Queries the ``clusterversions`` resource under ``config.openshift.io``,
    which only exists on actual OpenShift clusters.
    """
    if not _KUBE_AVAILABLE:
        return False
    try:
        custom_api = client.CustomObjectsApi(api_client)
        custom_api.list_cluster_custom_object(
            group="config.openshift.io",
            version="v1",
            plural="clusterversions",
        )
        return True
    except ApiException:
        return False


def get_service_endpoint(
    api_client: client.ApiClient,
    namespace: str,
    service_name: str,
) -> str | None:
    """Return ``ip:port`` for a Service, or None on failure."""
    if not _KUBE_AVAILABLE:
        return None
    v1 = client.CoreV1Api(api_client)
    try:
        service = v1.read_namespaced_service(
            name=service_name, namespace=namespace
        )
        cluster_ip = service.spec.cluster_ip
        if service.spec.ports:
            port = service.spec.ports[0].port
            return f"{cluster_ip}:{port}"
        return cluster_ip
    except ApiException:
        return None


def get_gateway_address(
    api_client: client.ApiClient,
    namespace: str,
    gateway_name: str,
) -> str | None:
    """Return the first address from a Gateway CR's status, or None."""
    if not _KUBE_AVAILABLE:
        return None
    custom_api = client.CustomObjectsApi(api_client)
    try:
        gateway = custom_api.get_namespaced_custom_object(
            group="gateway.networking.k8s.io",
            version="v1",
            namespace=namespace,
            plural="gateways",
            name=gateway_name,
        )
        addresses = gateway.get("status", {}).get("addresses", [])
        if addresses:
            return addresses[0].get("value")
    except ApiException:
        pass
    return None


def load_stacks_info(context: ExecutionContext) -> list[dict]:
    """Read per-stack config from all rendered stack directories."""
    stacks: list[dict] = []
    for stack_path in context.rendered_stacks or []:
        config_file = stack_path / "config.yaml"
        if not config_file.exists():
            continue
        try:
            with open(config_file, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if not cfg:
                continue
            standalone = cfg.get("standalone", {}).get("enabled", False)
            modelservice = cfg.get("modelservice", {}).get("enabled", False)
            if standalone:
                method = "standalone"
            elif modelservice:
                method = "modelservice"
            else:
                method = "unknown"
            stacks.append(
                {
                    "stack_name": stack_path.name,
                    "namespace": cfg.get("namespace", {}).get("name", "unknown"),
                    "model_name": (
                        cfg.get("model", {}).get("huggingfaceId")
                        or cfg.get("model", {}).get("name", "unknown")
                    ),
                    "method": method,
                }
            )
        except (OSError, yaml.YAMLError):
            continue
    return stacks


def print_phase_banner(
    context: ExecutionContext,
    *,
    extra_fields: dict[str, str] | None = None,
) -> None:
    """Print a bordered phase summary banner with cluster and stack info."""
    log = context.logger
    if not log:
        return

    from llmdbenchmark import __version__

    phase_label = context.current_phase.name
    is_dry_run = context.dry_run
    server = context.cluster_server or "unknown"
    platform = context.platform_type
    username = context.username or "unknown"
    num_stacks = len(context.rendered_stacks or [])
    stacks_info = load_stacks_info(context) if num_stacks > 1 else []

    namespace = context.namespace or "unknown"
    model = context.model_name or "unknown"
    methods = (
        ", ".join(context.deployed_methods) if context.deployed_methods else "default"
    )

    W = 60
    saved_indent = getattr(log, "_indent_level", 0)
    log.set_indent(0)

    log.line_break()
    log.log_info("═" * W)
    title = f"{phase_label} -- llm-d-benchmark v{__version__}"
    log.log_info(f"  {title}")
    log.log_info("─" * W)

    if context.specification_file:
        log.log_info(f"  Spec:        {context.specification_file}")

    if is_dry_run:
        log.log_info(f"  Mode:        DRY RUN (no cluster connection)")
    else:
        log.log_info(f"  User:        {username}")
        log.log_info(f"  Server:      {server}")
        log.log_info(f"  Platform:    {platform}")

    log.log_info("─" * W)

    if num_stacks <= 1:
        log.log_info(f"  Model:       {model}")
        log.log_info(f"  Methods:     {methods}")
        log.log_info(f"  Namespace:   {namespace}")
    else:
        log.log_info(f"  Stacks:      {num_stacks}")
        log.log_info("─" * W)
        for i, si in enumerate(stacks_info, 1):
            s_model = si.get("model_name", "unknown")
            s_ns = si.get("namespace", "unknown")
            s_method = si.get("method", "unknown")
            s_name = si.get("stack_name", f"stack-{i}")
            log.log_info(f"  Stack {i}: {s_name}")
            log.log_info(f"    Model:     {s_model}")
            log.log_info(f"    Namespace: {s_ns}")
            log.log_info(f"    Method:    {s_method}")
            if i < len(stacks_info):
                log.log_info("")

    if extra_fields:
        log.log_info("─" * W)
        for label, value in extra_fields.items():
            padded_label = f"{label}:"
            log.log_info(f"  {padded_label:<13} {value}")

    log.log_info("═" * W)
    log.line_break()

    log.set_indent(saved_indent)


def _kube_api_connect(context: ExecutionContext) -> None:
    """Connect to the Kubernetes API and set ``context.is_openshift``."""
    if not _KUBE_AVAILABLE:
        raise RuntimeError(
            "Kubernetes Python client is not installed; "
            "cannot verify cluster connectivity. "
            "Install with: pip install kubernetes"
        )

    try:
        api_client = kube_connect(
            kubeconfig=context.kubeconfig,
            kube_context=context.context_name,
            cluster_url=context.cluster_url,
            token=context.cluster_token,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load Kubernetes configuration: {exc}. "
            "Ensure a valid kubeconfig is available (via --kubeconfig, "
            "KUBECONFIG env var, ~/.kube/config, or in-cluster config)."
        ) from exc

    try:
        api = client.ApisApi(api_client)
        groups = api.get_api_versions()
    except Exception as exc:
        raise RuntimeError(
            f"Cluster is unreachable: {exc}. "
            "Loaded kubeconfig successfully but cannot communicate with "
            "the API server. Check network connectivity, credentials, "
            "and that the cluster is running."
        ) from exc

    try:
        custom_api = client.CustomObjectsApi(api_client)
        custom_api.list_cluster_custom_object(
            group="config.openshift.io",
            version="v1",
            plural="clusterversions",
        )
        context.is_openshift = True
    except Exception:  # pylint: disable=broad-exception-caught
        context.is_openshift = False


def _detect_local_platform(cmd: CommandExecutor, context: ExecutionContext) -> None:
    """Detect Kind or Minikube by inspecting kube-system pods."""
    if context.is_openshift:
        return

    result = cmd.kube(
        "get",
        "pods",
        "-o",
        "jsonpath={.items[*].metadata.name}",
        namespace="kube-system",
        check=False,
    )
    pod_names = result.stdout if result.success else ""

    if "kindnet" in pod_names or "kind-cluster" in pod_names:
        context.is_kind = True
    elif "etcd-minikube" in pod_names or "minikube" in pod_names:
        context.is_minikube = True


def _store_kubeconfig(cmd: CommandExecutor, context: ExecutionContext) -> None:
    """Create a self-contained ``context.ctx`` kubeconfig in the workspace."""
    env_dir = context.environment_dir()
    context_file = env_dir / "context.ctx"
    log = context.logger

    if context_file.exists():
        if _is_context_stale(cmd, context_file, context):
            if log:
                log.log_warning(
                    "Removing stale context.ctx (points to a different cluster)"
                )
            context_file.unlink()

    if not context_file.exists():
        created = False

        if context.kubeconfig and Path(context.kubeconfig).is_file():
            shutil.copy2(context.kubeconfig, context_file)
            created = True
            if log:
                log.log_info(f"Stored kubeconfig from {context.kubeconfig}")

        if not created:
            cluster_name = _get_cluster_name(cmd)
            if cluster_name:
                named_config = Path.home() / ".kube" / f"config-{cluster_name}"
                if named_config.is_file():
                    shutil.copy2(named_config, context_file)
                    created = True
                    if log:
                        log.log_info(f"Stored kubeconfig from {named_config}")

        if not created:
            extracted = _extract_current_context(cmd, context_file)
            if extracted:
                created = True
                if log:
                    log.log_info("Extracted current kubeconfig context context.ctx")

        if (
            not created
            and context.is_openshift
            and context.cluster_url
            and context.cluster_token
        ):
            _kube_login_and_store(cmd, context, context_file)
            created = context_file.exists()
            if created and log:
                log.log_info("Stored kubeconfig via kube login to context.ctx")

        if not created and log:
            log.log_warning(
                "Could not create context.ctx -- "
                "subsequent steps will use default kubeconfig"
            )

    if context_file.exists():
        context.kubeconfig = str(context_file)
        if log:
            log.log_info(f"Using kubeconfig: {context_file}")


def _is_context_stale(
    cmd: CommandExecutor,
    context_file: Path,
    context: ExecutionContext,
) -> bool:
    """Return True if the stored context.ctx points to a different cluster than the active kubeconfig."""
    try:
        stored_data = _read_kubeconfig_json(cmd, kubeconfig_override=str(context_file))
        stored_server = (
            _server_from_kubeconfig_data(stored_data) if stored_data else None
        )
        if not stored_server:
            return True  # Can't determine -- treat as stale

        current_data = _read_kubeconfig_json(cmd)
        current_server = (
            _server_from_kubeconfig_data(current_data) if current_data else None
        )
        if not current_server:
            return False  # Can't determine current -- keep stored

        return stored_server != current_server
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _get_cluster_name(cmd: CommandExecutor) -> str | None:
    """Derive a short cluster name (hostname) from the current API server."""
    data = _read_kubeconfig_json(cmd)
    if not data:
        return None
    server = _server_from_kubeconfig_data(data)
    if not server:
        return None
    parsed = urlparse(server)
    return parsed.hostname


def _extract_current_context(cmd: CommandExecutor, target_file: Path) -> bool:
    """Extract the current kubeconfig context into a self-contained file."""
    result = cmd.kube(
        "config",
        "view",
        "--minify",
        "--flatten",
        "--raw",
        check=False,
        force=True,  # Local-only read -- must run even in dry-run
    )
    if result.success and result.stdout.strip():
        target_file.write_text(result.stdout)
        return True
    return False


def _kube_login_and_store(
    cmd: CommandExecutor,
    context: ExecutionContext,
    target_file: Path,
) -> None:
    """Run ``oc login`` and extract the resulting kubeconfig."""
    server = context.cluster_url
    if not server.startswith("http"):
        server = f"https://{server}"
    if ":6443" not in server and ":443" not in server:
        server = f"{server}:6443"

    cmd.kube(
        "login",
        f"--token={context.cluster_token}",
        f"--server={server}",
        "--insecure-skip-tls-verify=true",
        check=False,
    )
    _extract_current_context(cmd, target_file)


def _read_kubeconfig_json(
    cmd: CommandExecutor, kubeconfig_override: str | None = None
) -> dict | None:
    """Run ``kubectl config view -o json`` and return the parsed output, or None."""
    args = ["config", "view", "-o", "json"]
    if kubeconfig_override:
        args = ["--kubeconfig", kubeconfig_override] + args

    result = cmd.kube(*args, check=False, force=True)  # Local-only read
    if not result.success:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _server_from_kubeconfig_data(data: dict) -> str | None:
    """Extract the API server URL from parsed kubeconfig JSON."""
    current_ctx = data.get("current-context", "")
    if not current_ctx:
        return None
    for ctx in data.get("contexts", []):
        if ctx.get("name") == current_ctx:
            cluster_ref = ctx.get("context", {}).get("cluster", "")
            for cluster_entry in data.get("clusters", []):
                if cluster_entry.get("name") == cluster_ref:
                    return cluster_entry.get("cluster", {}).get("server")
    return None


def _resolve_cluster_metadata(cmd: CommandExecutor, context: ExecutionContext) -> None:
    """Populate cluster_server, cluster_name, context_name, and username."""
    _resolve_username(context)

    data = _read_kubeconfig_json(cmd)
    if not data:
        return

    context.context_name = data.get("current-context", "")

    for ctx in data.get("contexts", []):
        if ctx.get("name") == context.context_name:
            cluster_ref = ctx.get("context", {}).get("cluster", "")
            for cluster_entry in data.get("clusters", []):
                if cluster_entry.get("name") == cluster_ref:
                    server = cluster_entry.get("cluster", {}).get("server", "")
                    context.cluster_server = server
                    parsed = urlparse(server)
                    context.cluster_name = parsed.hostname
                    break
            break


def _resolve_username(context: ExecutionContext) -> None:
    """Resolve the OS username for labeling purposes."""
    try:
        context.username = getpass.getuser()
    except Exception:  # pylint: disable=broad-exception-caught
        context.username = "unknown"
