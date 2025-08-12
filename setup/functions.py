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
                    result = subprocess.run(actual_cmd, shell=True, stdout=f_out, stderr=f_err, check=False)
            elif not verbose and not silent:
                # run with no log
                result = subprocess.run(actual_cmd, shell=True, check=False)
            else:
                # run with verbose
                announce(msg)
                result = subprocess.run(actual_cmd, shell=True, check=False)

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

    delete_cmd = f"{kcmd} delete job {job_name} -n {namespace} --ignore-not-found=true"

    announce(f"--> Deleting previous job '{job_name}' (if it exists) to prevent conflicts...")
    llmdbench_execute_cmd(
        actual_cmd=delete_cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=True
    )

    apply_cmd = f"{kcmd} apply -n {namespace} -f {yaml_file_path}"
    llmdbench_execute_cmd(
        actual_cmd=apply_cmd,
        dry_run=dry_run,
        verbose=verbose,
        silent=True,
        attempts=1
    )


async def wait_for_job(job_name, namespace, timeout=7200):
    """Wait for the  job to complete"""
    announce(f"Waiting for job {job_name} to complete...")

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

    #  split the model name into provider and rest
    provider, model_part = model.split('/', 1) if '/' in model else ("", model)

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
