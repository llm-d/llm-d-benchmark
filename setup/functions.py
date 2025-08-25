import re
from datetime import datetime
from typing import Union
import sys
import os
import time
from pathlib import Path
import subprocess
import inspect
import pykube
import hashlib
from pykube.exceptions import PyKubeError

import yaml

import kubernetes
from kubernetes import client as k8s_client, config as k8s_config

from kubernetes_asyncio import client as k8s_async_client
from kubernetes_asyncio import config as k8s_async_config
from kubernetes_asyncio import watch as k8s_async_watch

import asyncio

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def announce(message: str, logfile : str = None):
    work_dir = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", '.')
    log_dir = os.path.join(work_dir, 'logs')

    # ensure logs dir exists
    os.makedirs(log_dir, exist_ok=True)


    if not logfile:
        cur_step = os.getenv("CURRENT_STEP_NAME", 'step')
        logfile = cur_step + '.log'

    logpath = os.path.join(log_dir, logfile)

    logger.info(message)

    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_line = f"{timestamp} : {message}"
        with open(logpath, 'a', encoding='utf-8') as f:
            f.write(log_line + '\n')
    except IOError as e:
        logger.error(f"Could not write to log file '{logpath}'. Reason: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred with logfile '{logpath}'. Reason: {e}")



def kube_connect(config_path : str = '~/.kube/config'):
    api = None
    try:
        api = pykube.HTTPClient(pykube.KubeConfig.from_file(os.path.expanduser(config_path)))
    except FileNotFoundError:
        print("Kubeconfig file not found. Ensure you are logged into a cluster.")
        sys.exit(1)

    return api

class SecurityContextConstraints(pykube.objects.APIObject):
    version = "security.openshift.io/v1"
    endpoint = "securitycontextconstraints"
    kind = "SecurityContextConstraints"

def is_openshift(api: pykube.HTTPClient) -> bool:
    try:
        # the priviledged scc is a standard built in component for oc
        # if we get we are on oc
        SecurityContextConstraints.objects(api).get(name="privileged")
        announce("OpenShift cluster detected")
        return True
    except PyKubeError as e:
        if isinstance(e, pykube.exceptions.ObjectDoesNotExist):
            announce("'privileged' not found (not OpenShift)")
            return False
        # a 404 error means the scc resource type itself doesnt exist
        if e.code == 404:
            announce("Standard Kubernetes cluster detected (not OpenShift)")
            return False
        # for other errors like 403, we might be on OpenShift but lack permissions
        #  if we cant query sccs we cant modify them either
        announce(f'Could not query SCCs due to an API error (perhaps permissions?): {e}. Assuming not OpenShift for SCC operations')
        return False
    except Exception as e:
        #  other potential non pykube errors
        announce(f'An unexpected error occurred while checking for OpenShift: {e}. Assuming not OpenShift for SCC operations')
        return False

def llmdbench_execute_cmd(
    actual_cmd: str,
    dry_run: bool = True,
    verbose: bool = False,
    silent: bool = True,
    attempts: int = 1,
    fatal: bool = False,
    delay: int = 10
) -> int:
    work_dir_str = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    log_dir = Path(work_dir_str) / "setup" / "commands"

    log_dir.mkdir(parents=True, exist_ok=True)

    command_tstamp = int(time.time() * 1_000_000_000)

    if dry_run:
        msg = f"---> would have executed the command \"{actual_cmd}\""
        announce(msg)
        try:
            (log_dir / f"{command_tstamp}_command.log").write_text(msg + '\n')
        except IOError as e:
            announce(f"Error writing to dry run log: {e}")
        return 0

    if verbose:
        msg = f"---> will execute the command \"{actual_cmd}\""
        try:
            (log_dir / f"{command_tstamp}_command.log").write_text(msg + '\n')
        except IOError as e:
            announce(f"Error writing to command log: {e}")

    ecode = -1
    last_stdout_log = None
    last_stderr_log = None

    for counter in range(1, attempts + 1):
        command_tstamp = int(time.time() * 1_000_000_000)

        # log file paths
        stdout_log = log_dir / f"{command_tstamp}_stdout.log"
        stderr_log = log_dir / f"{command_tstamp}_stderr.log"
        last_stdout_log = stdout_log
        last_stderr_log = stderr_log

        try:
            # mimics the if/elif/else for verbose/silent
            if not verbose and silent:
                # correspon to eval with writing log
                with open(stdout_log, 'w') as f_out, open(stderr_log, 'w') as f_err:
                    result = subprocess.run(actual_cmd, shell=True, executable="/bin/bash", stdout=f_out, stderr=f_err, check=False)
            elif not verbose and not silent:
                # run with no log
                result = subprocess.run(actual_cmd, shell=True, executable="/bin/bash", check=False)
            else:
                # run with verbose
                announce(msg)
                result = subprocess.run(actual_cmd, shell=True, executable="/bin/bash", check=False)

            ecode = result.returncode

        except Exception as e:
            announce(f"An unexpected error occurred while running the command: {e}")
            ecode = -1

        if ecode == 0:
            break

        if counter < attempts:
            announce(f"Command failed with exit code {ecode}. Retrying in {delay} seconds... ({counter}/{attempts})")
            time.sleep(delay)

    if ecode != 0:
        announce(f"\nERROR while executing command \"{actual_cmd}\"")

        if last_stdout_log and last_stdout_log.exists():
            try:
                announce(last_stdout_log.read_text())
            except IOError:
                announce("(stdout not captured)")
        else:
            announce("(stdout not captured)")

        # print stderr log if it exists
        if last_stderr_log and last_stderr_log.exists():
            try:
                announce(last_stderr_log.read_text())
            except IOError:
                announce("(stderr not captured)")
        else:
            announce("(stderr not captured)")

    if fatal and ecode != 0:
        announce(f"\nFATAL: Exiting with code {ecode}.")
        sys.exit(ecode)

    return ecode



def environment_variable_to_dict(ev: dict = {}) :
    for key in dict(os.environ).keys():
        if "LLMDBENCH_" in key:
            ev.update({key.split("LLMDBENCH_")[1].lower():os.environ.get(key)})

    for mandatory_key in [ "control_dry_run", "control_verbose", "run_experiment_analyze_locally"] :
        if mandatory_key not in ev :
            ev[mandatory_key] = 0

        ev[mandatory_key] = bool(int(ev[mandatory_key]))

    ev["infra_dir"] = ev.get("infra_dir", "/tmp")
    ev["infra_git_repo"]  = ev.get("infra_git_repo", "https://github.com/llm-d-incubation/llm-d-infra.git")
    ev["infra_git_branch"] = ev.get("infra_git_branch", "main")
    ev["control_deploy_host_os"] = ev.get("control_deploy_host_os", "mac")
    ev["control_deploy_host_shell"] = ev.get("control_deploy_host_shell", "bash")
    ev["harness_conda_env_name"] = ev.get("harness_conda_env_name", "llmdbench-env")
    ev["control_work_dir"] = ev.get("control_work_dir", ".")
    ev["control_kcmd"] = ev.get("control_kcmd", "kubectl")



def create_namespace(api: pykube.HTTPClient, namespace_name: str, dry_run: bool = False, verbose: bool = False):
    if not namespace_name:
        announce("Error: namespace_name cannot be empty.")
        return

    announce(f"Ensuring namespace '{namespace_name}' exists...")

    ns = pykube.Namespace(api, {"metadata": {"name": namespace_name}})

    try:
        if ns.exists():
            announce(f"Namespace '{namespace_name}' already exists.")
        else:
            if dry_run:
                announce(f"[DRY RUN] Would have created namespace '{namespace_name}'.")
            else:
                ns.create()
                announce(f"✅ Namespace '{namespace_name}' created successfully.")
    except PyKubeError as e:
        announce(f"Failed to create or check namespace '{namespace_name}': {e}")


def validate_and_create_pvc(
    api: pykube.HTTPClient,
    namespace: str,
    download_model: str,
    pvc_name: str,
    pvc_size: str,
    pvc_class: str,
    dry_run: bool = False
):
    announce("Provisioning model storage…")

    if '/' not in download_model:
        announce(f"'{download_model}' is not in Hugging Face format <org>/<repo>")
        sys.exit(1)

    announce(f"🔍 Checking storage class '{pvc_class}'...")
    try:
        k8s_config.load_kube_config()
        storage_v1_api = k8s_client.StorageV1Api()

        if pvc_class == "default" :
            for x in storage_v1_api.list_storage_class().items :
                if x.metadata.annotations and "storageclass.kubernetes.io/is-default-class" in x.metadata.annotations :
                    if x.metadata.annotations["storageclass.kubernetes.io/is-default-class"] == "true" :
                        announce(f"ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS automatically set to \"{x.metadata.name}\"")
                        pvc_class = x.metadata.name
        storage_v1_api.read_storage_class(name=pvc_class)
        announce(f"StorageClass '{pvc_class}' found.")

    except k8s_client.ApiException as e:
        # if returns a 404 the storage class doesnt exist
        if e.status == 404:
            announce(f"StorageClass '{pvc_class}' not found")
            sys.exit(1)
        else:
            # handle other
            announce(f"❌ Error checking StorageClass: {e}")
            sys.exit(1)
    except FileNotFoundError:
        announce("❌ Kubeconfig file not found. Cannot check StorageClass.")
        sys.exit(1)

    pvc_obj = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": namespace,
        },
        "spec": {
            "accessModes": ["ReadWriteMany"],
            "resources": {
                "requests": {"storage": pvc_size}
            },
            "storageClassName": pvc_class,
            "volumeMode": "Filesystem"
        }
    }

    pvc = pykube.PersistentVolumeClaim(api, pvc_obj)

    try:
        if pvc.exists():
            announce(f"PVC '{pvc_name}' already exists in namespace '{namespace}'.")
        else:
            if dry_run:
                announce(f"[DRY RUN] Would have created PVC '{pvc_name}' in namespace '{namespace}'.")
            else:
                pvc.create()
                announce(f"PVC '{pvc_name}' created successfully.")
    except PyKubeError as e:
        announce(f"Failed to create or check PVC '{pvc_name}': {e}")
        sys.exit(1)


def launch_download_job(
    namespace: str,
    secret_name: str,
    download_model: str,
    model_path: str,
    pvc_name: str,
    dry_run: bool = False,
    verbose: bool = False
):

    work_dir_str = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    current_step = os.getenv("LLMDBENCH_CURRENT_STEP", "step")
    kcmd = os.getenv("LLMDBENCH_CONTROL_KCMD", "kubectl")

    work_dir = Path(work_dir_str)
    yaml_dir = work_dir / "setup" / "yamls"
    yaml_dir.mkdir(parents=True, exist_ok=True)
    yaml_file_path = yaml_dir / f"{current_step}_download_pod_job.yaml"

    announce("Launching model download job...")

    command_args = (
        'mkdir -p "${MOUNT_PATH}/${MODEL_PATH}" && '
        'pip install huggingface_hub && '
        'export PATH="${PATH}:${HOME}/.local/bin" && '
        'hf auth login --token "${HF_TOKEN}" && '
        'hf download "${HF_MODEL_ID}" --local-dir "/cache/${MODEL_PATH}"'
    )

    job_name = 'download-model'


    job_yaml = f"""
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
spec:
  backoffLimit: 3
  template:
    spec:
      containers:
        - name: downloader
          image: python:3.10
          command: ["/bin/sh", "-c"]
          args:
            - |
              {command_args}
          env:
            - name: MODEL_PATH
              value: {model_path}
            - name: HF_MODEL_ID
              value: {download_model}
            - name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {secret_name}
                  key: HF_TOKEN
            - name: HF_HOME
              value: /tmp/huggingface
            - name: HOME
              value: /tmp
            - name: MOUNT_PATH
              value: /cache
          volumeMounts:
            - name: model-cache
              mountPath: /cache
      restartPolicy: OnFailure
      volumes:
        - name: model-cache
          persistentVolumeClaim:
            claimName: {pvc_name}
"""

    try:
        yaml.safe_load(job_yaml) # validate yaml
        yaml_file_path.write_text(job_yaml)
        announce(f"Generated YAML file at: {yaml_file_path}")
    except IOError as e:
        announce(f"Error writing YAML file: {e}")
        sys.exit(1)

    #FIXME (USE PYKUBE)
    delete_cmd = f"{kcmd} delete job {job_name} -n {namespace} --ignore-not-found=true"
    announce(f"--> Deleting previous job '{job_name}' (if it exists) to prevent conflicts...")
    llmdbench_execute_cmd(
        actual_cmd=delete_cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=True
    )
    #FIXME (USE PYKUBE)
    apply_cmd = f"{kcmd} apply -n {namespace} -f {yaml_file_path}"
    llmdbench_execute_cmd(
        actual_cmd=apply_cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=True,
        attempts=1
    )


async def wait_for_job(job_name, namespace, timeout=7200, dry_run: bool = False):
    """Wait for the  job to complete"""
    announce(f"Waiting for job {job_name} to complete...")

    if dry_run :
        return True

    # use async config loading
    await k8s_async_config.load_kube_config()
    api_client = k8s_async_client.ApiClient()
    batch_v1_api = k8s_async_client.BatchV1Api(api_client)
    try:

        w = k8s_async_watch.Watch()

        # sets up connection with kubernetes, async with manages the streams lifecycle
        async with w.stream(
            func=batch_v1_api.list_namespaced_job,
            namespace=namespace,
            field_selector=f"metadata.name={job_name}",
            timeout_seconds=timeout  # replaces the manual timeout check
        ) as stream:

            async for event in stream: # replaces time.wait since we grab events as they come from stream sasynchronous
                job_status = event['object'].status
                if job_status.succeeded:
                    announce(f"Evaluation job {job_name} completed successfully.")
                    return True

                elif job_status.failed:
                    announce(f"Evaluation job {job_name} failed")
                    return False


    except asyncio.TimeoutError:
        announce(f"Timeout waiting for evaluation job {job_name} after {timeout} seconds.")
        return False
    except Exception as e:
        announce(f"Error occured while waiting for job {job_name} : {e}")
    finally:
        await api_client.close()

def model_attribute(model: str, attribute: str) -> str:

    model, modelid = model.split(':', 1) if ':' in model else (model, model)
    modelid = modelid.replace('/', '-').replace('.','-')

    #  split the model name into provider and rest
    provider, model_part = model.split('/', 1) if '/' in model else ("", model)

    hash_object = hashlib.sha256()
    hash_object.update(modelid.encode('utf-8'))
    digest = hash_object.hexdigest()
    modelid_label = f"{provider[:8]}-{digest[:8]}-{model_part[-8:]}"

    # create a list of components from the model part
    # equiv  to: tr '[:upper:]' '[:lower:]' | sed -e 's^qwen^qwen-^g' -e 's^-^\n^g'
    model_components_str = model_part.lower().replace("qwen", "qwen-")
    model_components = model_components_str.split('-')

    # get individual attributes using regex
    type_str = ""
    for comp in model_components:
        if re.search(r"nstruct|hf|chat|speech|vision|opt", comp, re.IGNORECASE):
            type_str = comp
            break

    parameters = ""
    for comp in model_components:
        if re.search(r"[0-9].*[bm]", comp, re.IGNORECASE):
            parameters = re.sub(r'^[a-z]', '', comp, count=1)
            parameters = parameters.replace('.', 'p')
            break

    major_version = "1"
    for comp in model_components:
        # find component that starts with a digit but is not the parameter string
        if comp.isdigit() or (comp and comp[0].isdigit() and not re.search(r"b|m", comp, re.IGNORECASE)):
            # remove the parameter string from it if present ... for case like like "3.1-8B"
            version_part = comp.replace(parameters, "")
            major_version = version_part.split('.')[0]
            break

    kind = model_components[0] if model_components else ""

    as_label = model.lower().replace('/', '-').replace('.', '-')

    # build label and clean it up
    label_parts = [part for part in [kind, major_version, parameters] if part]
    label = '-'.join(label_parts)
    label = re.sub(r'-+', '-', label).strip('-') # replace multiple hyphens and strip from ends

    folder = model.lower().replace('/', '_').replace('-', '_')

    # storing all attributes in a dictionary
    attributes = {
        "model": model,
        "modelid": modelid,
        "modelid_label": modelid_label,
        "provider": provider,
        "type": type_str,
        "parameters": parameters,
        "majorversion": major_version,
        "kind": " ".join(kind.split("_")),
        "as_label": as_label,
        "label": label,
        "folder": folder,
    }

    # return requested attrib
    result = attributes.get(attribute, "")

    # The original script lowercases everything except the model attribute
    if attribute != "model":
        return result.lower()
    else:
        return result

#FIXME (USE PYKUBE)
def apply_configmap(yaml_file: Path, kubectl_cmd: str, dry_run: bool, verbose: bool) -> int:
    """
    Apply ConfigMap using kubectl/oc command.

    Args:
        yaml_file: Path to the YAML file to apply
        kubectl_cmd: kubectl or oc command to use
        dry_run: If True, only print what would be executed
        verbose: If True, print detailed output

    Returns:
        int: Command exit code (0 for success)
    """
    cmd = f"{kubectl_cmd} apply -f {yaml_file}"

    return llmdbench_execute_cmd(
        actual_cmd=cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=not verbose
    )


def extract_environment():
    """
    Extract and display environment variables for debugging.
    Equivalent to the bash extract_environment function.
    """

    ev = {}
    for key, value in os.environ.items():
        if "LLMDBENCH_" in key:
            ev[key.split("LLMDBENCH_")[1].lower()] = value

    # Get environment variables that start with LLMDBENCH, excluding sensitive ones
    env_vars = []
    for key, value in os.environ.items():
        if key.startswith("LLMDBENCH_") and not any(sensitive in key.upper() for sensitive in ["TOKEN", "USER", "PASSWORD", "EMAIL"]):
            env_vars.append(f"{key}={value}")

    env_vars.sort()

    # Check if environment variables have been displayed before
    envvar_displayed = int(os.environ.get("LLMDBENCH_CONTROL_ENVVAR_DISPLAYED", 0))

    if envvar_displayed == 0:
        print("\n\nList of environment variables which will be used")
        for var in env_vars:
            print(var)
        print("\n\n")
        os.environ["LLMDBENCH_CONTROL_ENVVAR_DISPLAYED"] = "1"

    # Write environment variables to file
    work_dir = os.environ.get("LLMDBENCH_CONTROL_WORK_DIR", ".")
    env_dir = Path(work_dir) / "environment"
    env_dir.mkdir(parents=True, exist_ok=True)

    with open(env_dir / "variables", "w") as f:
        for var in env_vars:
            f.write(var + "\n")


def get_image(image_registry: str, image_repo: str, image_name: str, image_tag: str, tag_only: str = "0") -> str:
    """
    Construct container image reference.
    Equivalent to the bash get_image function.

    Args:
        image_registry: Container registry
        image_repo: Repository/organization
        image_name: Image name
        image_tag: Image tag
        tag_only: If "1", return only the tag

    Returns:
        Full image reference or just tag
    """
    is_latest_tag = image_tag

    if image_tag == "auto":
        ccmd = os.getenv("LLMDBENCH_CONTROL_CCMD", "skopeo")
        image_full_name = f"{image_registry}/{image_repo}/{image_name}"

        if ccmd == "podman":
            # Use podman search to get latest tag
            cmd = f"{ccmd} search --list-tags {image_full_name}"
            try:
                result = subprocess.run(cmd.split(), capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    if len(lines) > 0:
                        # Get the last line and extract the tag (second column)
                        last_line = lines[-1]
                        parts = last_line.split()
                        if len(parts) >= 2:
                            is_latest_tag = parts[1]
                # The || true part in bash means we don't fail if command fails
            except:
                pass
        else:
            # Use skopeo to get latest tag
            cmd = f"skopeo list-tags docker://{image_full_name}"
            try:
                result = subprocess.run(cmd.split(), capture_output=True, text=True, check=True)
                import json
                tags_data = json.loads(result.stdout)
                if tags_data.get("Tags"):
                    # Use jq -r .Tags[] | tail -1 equivalent
                    is_latest_tag = tags_data["Tags"][-1]
            except:
                is_latest_tag = ""

        if not is_latest_tag:
            announce(f"❌ Unable to find latest tag for image \"{image_full_name}\"")
            sys.exit(1)

    if tag_only == "1":
        return is_latest_tag
    else:
        return f"{image_registry}/{image_repo}/{image_name}:{is_latest_tag}"
