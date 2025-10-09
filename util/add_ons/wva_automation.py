#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess
import logging
import argparse
import time
import tempfile

from pathlib import Path

try:
    import yaml
    import pykube
    from pykube import Pod
except ModuleNotFoundError as e:
    print("[Error]: Please install the following python requirements:")
    print("         pyaml, pykube")


def setup_logger() -> logging.Logger:
    """
    Configure and return a logger for WVA deployment operations.

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger("wva-deploy")
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stdout)
    error_handler = logging.StreamHandler(sys.stderr)

    console_handler.setLevel(logging.INFO)
    error_handler.setLevel(logging.ERROR)

    formatter = logging.Formatter("[%(levelname)s] %(message)s")
    console_handler.setFormatter(formatter)
    error_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(error_handler)

    return logger


def dependency_check(logger: logging.Logger) -> bool:
    """
    Verify that required dependencies are installed.

    Args:
        logger (logging.Logger): Logger for logging messages.

    Returns:
        bool: True if all dependencies are installed, False otherwise.
    """
    try:
        git_path = shutil.which("git")
    except Exception as e:
        logger.error(f"Failed to check git installation: {e}")
        return False

    try:
        helm_path = shutil.which("helm")
    except Exception as e:
        logger.error(f"Failed to check helm installation: {e}")
        return False

    return True


def clone_or_update_repo(
    repo_url: str, branch_name: str, logger: logging.Logger
) -> Path:
    """
    Clone the repository if it doesn't exist, or pull the latest changes if it does.

    Args:
        repo_url (str): Git repository URL.
        branch_name (str): Branch to checkout.
        logger (logging.Logger): Logger for logging messages.

    Returns:
        str: repo path
    """

    try:
        tmp_dir = tempfile.mkdtemp()
        repo_name = repo_url.split("/")[-1].replace(".git", "")
        repo_dir = Path(tmp_dir) / repo_name
        if not repo_dir.exists():
            logger.info(f"Cloning {repo_url} \n       WVA Directory: {repo_dir}")
            subprocess.run(
                ["git", "clone", "-b", branch_name, repo_url, str(repo_dir)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            logger.info(f"Repository exists at {repo_dir}. Fetching latest updates...")
            subprocess.run(
                ["git", "-C", str(repo_dir), "fetch"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(repo_dir), "checkout", branch_name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(repo_dir), "pull", "origin", branch_name],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(f"Repository updated to the latest '{branch_name}' branch.")
        return Path(repo_dir)
    except (subprocess.CalledProcessError, OSError, PermissionError) as e:
        logger.error(f"Git operation failed: {e}")
        return ""


def update_prometheus_config(
    prometheus_config_path: Path, cluster_type: str, logger: logging.Logger
) -> bool:
    """
    Update the Prometheus base URL in the WVA ConfigMap based on cluster type.

    Args:
        prometheus_config_path (Path): Path to the WVA prometheus config in repository directory.
        cluster_type (str): Type of cluster ('openshift' or 'kind').
        logger (logging.Logger): Logger for logging messages.

    Returns:
        bool: True if the configuration was updated successfully, False otherwise.
    """

    if not prometheus_config_path.exists():
        logger.error(f"ConfigMap file not found: {prometheus_config_path}")
        return False

    try:
        with prometheus_config_path.open("r") as f:
            config = yaml.safe_load(f)

        if cluster_type == "openshift":
            config["data"][
                "PROMETHEUS_BASE_URL"
            ] = "https://thanos-querier.openshift-monitoring.svc.cluster.local:9091"
        elif cluster_type == "kind":
            config["data"][
                "PROMETHEUS_BASE_URL"
            ] = "https://kube-prometheus-stack-prometheus.workload-variant-autoscaler-monitoring.svc.cluster.local:9090"
        else:
            logger.error(f"Unsupported cluster type: {cluster_type}")
            return False

        with prometheus_config_path.open("w") as f:
            yaml.dump(config, f, sort_keys=False)
        return True

    except (OSError, yaml.YAMLError) as e:
        logger.error(f"Failed to update Prometheus config: {e}")
        return False
    except KeyError as e:
        logger.error(f"ConfigMap missing expected key: {e}")
        return False


def deploy_wva(wva_dir: Path, deploy_image: str, logger: logging.Logger) -> bool:
    """
    Deploy the Workload Variant Autoscaler using the Makefile.

    Args:
        wva_dir (Path): WVA repository directory.
        deploy_image (str): Container image to use for deployment.
        logger (logging.Logger): Logger for logging messages.

    Returns:
        bool: True if deployment succeeds, False otherwise.
    """
    logger.info("Starting WVA deployment...")
    try:
        subprocess.run(
            ["make", "deploy", f"IMG={deploy_image}"],
            cwd=wva_dir,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Deployment completed successfully.")
        return True
    except FileNotFoundError:
        logger.error("Makefile not found or 'make' command missing in PATH.")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"Deployment failed: {e}")
        return False


def undeploy_wva(
    llmd_namespace: str,
    wva_namespace: str,
    monitoring_namespace: str,
    api: Path,
    wva_dir: Path,
    logger: logging.Logger,
) -> bool:
    """
    Undeploy the Workload Variant Autoscaler using the Makefile.

    Args:
        wva_namespace (str): Kubernetes namespace to clean.
        monitoring_namespace (str): Kubernetes namespace to clean.
        api (Path): Path to kubeconfig ctx.
        wva_dir (Path): WVA repository directory.
        logger (logging.Logger): Logger for logging messages.

    Returns:
        bool: True if undeployment succeeds, False otherwise.
    """
    logger.info("Starting WVA undeployment...")

    try:
        subprocess.run(
            ["make", "undeploy"],
            cwd=wva_dir,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Undeployment completed successfully.")
        return True

    except FileNotFoundError:
        logger.error("Makefile not found or 'make' command missing in PATH.")
        return False

    except subprocess.CalledProcessError as e:

        try:
            api = pykube.HTTPClient(
                pykube.KubeConfig.from_file(os.path.expanduser(api))
            )
        except Exception as e:
            logger.error(f"Failed to load kubeconfig or connect to cluster: {e}")
            return False

        try:
            pods = Pod.objects(api).filter(namespace=wva_namespace)
        except Exception as e:
            logger.error(
                f"Failed to list pods in namespace '{wva_namespace
            }': {e}"
            )
            return False

        if pods:
            logger.error(f"Undeployment failed: {e}")
            return False

    class ServiceMonitor(pykube.objects.NamespacedAPIObject):
        version = "monitoring.coreos.com/v1"
        endpoint = "servicemonitors"
        kind = "ServiceMonitor"

    def delete_resource(resource_class, name, namespace):
        try:
            obj = (
                resource_class.objects(api)
                .filter(namespace=namespace, selector={"metadata.name": name})
                .get()
            )
            obj.delete()
            logger.info(
                f"Deleted {resource_class.kind} '{name}' in namespace '{namespace}'"
            )
        except pykube.exceptions.ObjectDoesNotExist:
            logger.info(
                f"{resource_class.kind} '{name}' not found in namespace '{namespace}', skipping..."
            )

    def delete_servicemonitor(name, namespace):
        try:
            sm = ServiceMonitor.objects(api).filter(namespace=namespace).get(name=name)
            sm.delete()
            logger.info(f"Deleted ServiceMonitor '{name}' in namespace '{namespace}'")
        except pykube.exceptions.ObjectDoesNotExist:
            logger.info(
                f"ServiceMonitor '{name}' not found in namespace '{namespace}', skipping..."
            )

    delete_resource(
        pykube.HorizontalPodAutoscaler, "vllm-deployment-hpa", llmd_namespace
    )
    delete_resource(pykube.ConfigMap, "prometheus-ca", monitoring_namespace)
    delete_servicemonitor(
        "workload-variant-autoscaler-controller-manager-metrics-monitor",
        monitoring_namespace,
    )

    result = subprocess.run(
        [
            "helm",
            "uninstall",
            "prometheus-adapter",
            "-n",
            "prometheus-adapter",
            "--ignore-not-found",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        logger.info(
            f"Helm release 'prometheus-adapter' uninstalled successfully in namespace '{monitoring_namespace}'"
        )
    else:
        logger.error(
            f"Failed to uninstall Helm release 'prometheus-adapter' in namespace '{monitoring_namespace}'"
        )
        return False

    return True


def check_pods_running(
    namespace: str,
    api: Path,
    logger: logging.Logger,
    timeout: int = 30,
    interval: int = 5,
) -> bool:
    """
    Check if all pods in a given namespace are in Running state.

    Args:
        namespace (str): Kubernetes namespace to check pods in.
        api (Path): Path to kubeconfig ctx.
        logger (logging.Logger): Logger instance.
        timeout (int): Maximum seconds to wait for all pods to be running.
        interval (int): Seconds to wait between checks.

    Returns:
        bool: True if all pods are running, False otherwise.
    """
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_file(os.path.expanduser(api)))
    except Exception as e:
        logger.error(f"Failed to load kubeconfig or connect to cluster: {e}")
        return False

    try:
        pods = Pod.objects(api).filter(namespace=namespace)
    except Exception as e:
        logger.error(f"Failed to list pods in namespace '{namespace}': {e}")
        return False

    all_running = True

    start_time = time.time()
    while True:
        try:
            pods = Pod.objects(api).filter(namespace=namespace)
        except Exception as e:
            logger.error(f"Failed to list pods in namespace '{namespace}': {e}")
            return False

        all_running = True
        for pod in pods:
            try:
                pod_name = pod.name
                pod_status = pod.obj.get("status", {}).get("phase", "Unknown")
                logger.info(f"Pod {pod_name}: {pod_status}")
                if pod_status != "Running":
                    all_running = False
            except Exception as e:
                logger.error(f"Error reading pod '{pod_name}' status: {e}")
                all_running = False

        if all_running:
            logger.info(f"All pods in namespace '{namespace}' are running.")
            return True

        elapsed = time.time() - start_time
        if elapsed >= timeout:
            logger.warning(
                f"Timeout reached ({timeout}s). Some pods are still not running."
            )
            return False

        logger.info(f"Waiting {interval}s before re-checking pod statuses...")
        time.sleep(interval)


def main() -> int:
    """Main entry point for the WVA deployment script."""

    DEPLOY_STR = "deploy"
    UNDEPLOY_STR = "undeploy"

    try:
        logger = setup_logger()
    except Exception as e:
        print(f"[ERROR] Failed to initialize logger: {e}", file=sys.stderr)
        return 1
    try:
        parser = argparse.ArgumentParser(
            description="Deploy Workload Variant Autoscaler (WVA)."
        )
        parser.add_argument(
            "--action",
            choices=[DEPLOY_STR, UNDEPLOY_STR],
            required=True,
            help="Specify the action to perform: 'deploy' or 'undeploy'.",
        )
        parser.add_argument(
            "--git-repo-ssh",
            type=str,
            default="git@github.com:llm-d-incubation/workload-variant-autoscaler.git",
            help="Specify the repository to clone via ssh for WVA deployment utilities and configs.",
        )
        parser.add_argument(
            "--git-branch",
            type=str,
            default="main",
            help="Specify the repository branch to clone WVA deployment utilities and configs.",
        )
        parser.add_argument(
            "--wva-image",
            type=str,
            default="quay.io/infernoautoscaler/inferno-controller:0.0.1-multi-arch",
            help="WVA controller image to deploy. Format: <registry>/<repo>/<image>:<version>",
        )
        parser.add_argument(
            "--kubeconfig-ctx",
            type=str,
            required=True,
            help="Specify the path to the kubeconfig ctx.",
        )
        parser.add_argument(
            "--cluster-type",
            choices=["kind", "openshift"],
            required=True,
            help="Specify the cluster type: 'kind' or 'openshift'.",
        )
        parser.add_argument(
            "--monitoring-namespace",
            type=str,
            default="openshift-user-workload-monitoring",
            help="Namespace where monitoring stack is deployed.",
        )
        parser.add_argument(
            "--wva-namespace",
            type=str,
            default="workload-variant-autoscaler-system",
            help="Namespace where wva is deployed.",
        )
        parser.add_argument(
            "--llmd-namespace",
            type=str,
            required=True,
            help="Namespace where llmd is deployed.",
        )
        args = parser.parse_args()

        action = args.action
        git_repo_ssh = args.git_repo_ssh
        git_branch = args.git_branch
        wva_image = args.wva_image
        kubeconfig_ctx = args.kubeconfig_ctx
        cluster_type = args.cluster_type
        monitoring_namespace = args.monitoring_namespace
        wva_namespace = args.wva_namespace
        llmd_namespace = args.llmd_namespace

        current_file = Path(__file__).resolve()
        project_root = current_file.parents[1]
        sys.path.insert(0, str(project_root))

        if not dependency_check(logger):
            return 1

        repo_dir = clone_or_update_repo(git_repo_ssh, git_branch, logger)
        if repo_dir == "":
            return 1

        if action == DEPLOY_STR:

            logger.info(f"Starting deployment for cluster type: {cluster_type}")
            logger.info(f"Monitoring namespace: {monitoring_namespace}")

            if not update_prometheus_config(
                Path(f"{repo_dir}/config/manager/configmap.yaml"), cluster_type, logger
            ):
                return 1

            if not deploy_wva(repo_dir, wva_image, logger):
                return 1

            if not check_pods_running(wva_namespace, Path(kubeconfig_ctx), logger):
                return 1

            logger.info("WVA deployment finished successfully.")
            return 0

        elif action == UNDEPLOY_STR:

            if not undeploy_wva(
                llmd_namespace,
                wva_namespace,
                monitoring_namespace,
                Path(kubeconfig_ctx),
                repo_dir,
                logger,
            ):
                return 1

            logger.info("Finished undeploying WVA.")

        else:
            logger.error(f"Unsupported Action")
            return 1

    except Exception as e:
        logger.error(f"Unhandled exception: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
