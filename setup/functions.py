from dataclasses import dataclass
import re
from datetime import datetime
from typing import List, Tuple, Union, Any
import sys
import os
import time
from pathlib import Path
import subprocess
import requests
import inspect
import hashlib
import pykube
from pykube.query import Query
from pykube.exceptions import PyKubeError, ObjectDoesNotExist
from urllib3.exceptions import ProtocolError

import base64
import tempfile
import random
import string
import tempfile
import yaml

import kubernetes
from kubernetes import (
    client as k8s_client,
    config as k8s_config,
    stream as k8s_stream,
    utils as k8s_utils,
    watch as k8s_watch,
)
from kubernetes_asyncio import (
    client as k8s_async_client,
    config as k8s_async_config,
    watch as k8s_async_watch,
)

import asyncio

import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Import config_explorer module
current_file = Path(__file__).resolve()
workspace_root = current_file.parents[2]
try:
    from config_explorer.capacity_planner import (
        KVCacheDetail,
        gpus_required,
        get_model_info_from_hf,
        get_model_config_from_hf,
        get_text_config,
        find_possible_tp,
        max_context_len,
        available_gpu_memory,
        model_total_params,
        model_memory_req,
        allocatable_kv_cache_memory,
        kv_cache_req,
        max_concurrent_requests,
    )
except ModuleNotFoundError as e:
    print(f"❌ ERROR: Failed to import config_explorer module: {e}")
    print(
        f"\nTry: pip install -r {workspace_root / 'config_explorer' / 'requirements.txt'}"
    )
    sys.exit(1)
except Exception as e:
    print(
        f"❌ ERROR: An unexpected error occurred while importing config_explorer: {e}"
    )
    import traceback

    traceback.print_exc()
    sys.exit(1)

try:
    from transformers import AutoConfig
    from huggingface_hub import ModelInfo
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError
except ModuleNotFoundError as e:
    print(f"❌ ERROR: Required dependency not installed: {e}")
    print("Please install the required dependencies:")
    print(f"  pip install -r {workspace_root / 'config_explorer' / 'requirements.txt'}")
    sys.exit(1)


# Allows to properly have blocks in YAMLs
class LiteralStr(str):
    pass
def literal_str_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
yaml.add_representer(LiteralStr, literal_str_representer)


def announce(msgcont: str, logfile: str = None, ignore_if_failed: bool = False):
    work_dir = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    log_dir = os.path.join(work_dir, "logs")

    # ensure logs dir exists
    os.makedirs(log_dir, exist_ok=True)

    if not logfile:
        cur_step = os.getenv("CURRENT_STEP_NAME", "step")
        logfile = cur_step + ".log"

    logpath = os.path.join(log_dir, logfile)

    if msgcont.count("ERROR:"):
        msgcont = f"❌  {msgcont.replace('ERROR: ','')}"
        logger.error(msgcont)

    elif msgcont.count("WARNING:"):
        msgcont = f"⚠️  {msgcont.replace('WARNING: ','')}"
        logger.warn(msgcont)
    else:
        logger.info(msgcont)

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"{timestamp} : {msgcont}"
        with open(logpath, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")
    except IOError as e:
        logger.error(f"Could not write to log file '{logpath}'. Reason: {e}")
    except Exception as e:
        logger.error(
            f"An unexpected error occurred with logfile '{logpath}'. Reason: {e}"
        )

    if msgcont.count("ERROR:") and not ignore_if_failed:
        sys.exit(1)

def kube_connect(config_path: str = "~/.kube/config"):
    api = None
    try:
        api = pykube.HTTPClient(
            pykube.KubeConfig.from_file(os.path.expanduser(config_path))
        )
        k8s_config.load_kube_config(os.path.expanduser(config_path))
    except FileNotFoundError:
        print("Kubeconfig file not found. Ensure you are logged into a cluster.")
        sys.exit(1)

    return api, k8s_client

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
        announce(
            f"WARNING: Could not query SCCs due to an API error (perhaps permissions?): {e}. Assuming not OpenShift for SCC operations"
        )
        return False
    except Exception as e:
        #  other potential non pykube errors
        announce(
            f"WARNING: An unexpected error occurred while checking for OpenShift: {e}. Assuming not OpenShift for SCC operations"
        )
        return False

def clear_string(string_to_clear: str) -> str:
    clear_string_lines = []
    for line in string_to_clear.splitlines():
        if line.strip() and not line.count("#noconfig") and not line[0] == "#":
            clear_string_lines.append(line)

    clear_string = "\n".join(clear_string_lines)
    return clear_string

def llmdbench_execute_cmd(
    actual_cmd: str,
    dry_run: bool = True,
    verbose: bool = False,
    silent: bool = True,
    attempts: int = 1,
    fatal: bool = False,
    delay: int = 10,
) -> int:
    work_dir_str = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    log_dir = Path(work_dir_str) / "setup" / "commands"

    log_dir.mkdir(parents=True, exist_ok=True)

    command_tstamp = int(time.time() * 1_000_000_000)

    if dry_run:
        msg = f'---> would have executed the command "{actual_cmd}"'
        announce(msg)
        try:
            (log_dir / f"{command_tstamp}_command.log").write_text(msg + "\n")
        except IOError as e:
            announce(f"ERROR: unable to write to dry run log: {e}")
        return 0

    if verbose:
        msg = f'---> will execute the command "{actual_cmd}"'
        try:
            (log_dir / f"{command_tstamp}_command.log").write_text(msg + "\n")
        except IOError as e:
            announce(f"ERROR: unable to write to command log: {e}")

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
                with open(stdout_log, "w") as f_out, open(stderr_log, "w") as f_err:
                    result = subprocess.run(
                        actual_cmd,
                        shell=True,
                        executable="/bin/bash",
                        stdout=f_out,
                        stderr=f_err,
                        check=False,
                    )
            elif not verbose and not silent:
                # run with no log
                result = subprocess.run(
                    actual_cmd, shell=True, executable="/bin/bash", check=False
                )
            else:
                # run with verbose
                announce(msg)
                result = subprocess.run(
                    actual_cmd, shell=True, executable="/bin/bash", check=False
                )

            ecode = result.returncode

        except Exception as e:
            announce(f"An unexpected error occurred while running the command: {e}")
            ecode = -1

        if ecode == 0:
            break

        if counter < attempts:
            announce(
                f"Command failed with exit code {ecode}. Retrying in {delay} seconds... ({counter}/{attempts})"
            )
            time.sleep(delay)

    if ecode != 0:
        if not silent:
            announce(f'\nERROR: while executing command "{actual_cmd}"')

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
        announce(f"ERROR: Exiting with code {ecode}.")
        sys.exit(ecode)

    return ecode

def environment_variable_to_dict(ev: dict = {}):
    for key in dict(os.environ).keys():
        if "LLMDBENCH_" in key:
            ev.update({key.split("LLMDBENCH_")[1].lower(): os.environ.get(key)})

    # Convert true/false to boolean values
    for key, value in ev.items():
        if type(value) == str:
            value = value.lower()
            if value == "true":
                ev[key] = True
            if value == "false":
                ev[key] = False

    for mandatory_key in [
        "control_dry_run",
        "control_verbose",
        "run_experiment_analyze_locally",
        "user_is_admin",
        "control_environment_type_standalone_active",
        "control_environment_type_modelservice_active",
        "wva_enabled",
    ]:
        if mandatory_key not in ev:
            ev[mandatory_key] = 0

        ev[mandatory_key] = bool(int(ev[mandatory_key]))

    ev["infra_dir"] = ev.get("infra_dir", "/tmp")
    ev["infra_git_branch"] = ev.get("infra_git_branch", "main")
    ev["control_deploy_host_os"] = ev.get("control_deploy_host_os", "mac")
    ev["control_deploy_host_shell"] = ev.get("control_deploy_host_shell", "bash")
    ev["harness_conda_env_name"] = ev.get("harness_conda_env_name", "llmdbench-env")
    ev["control_work_dir"] = ev.get("control_work_dir", ".")
    ev["control_kcmd"] = ev.get("control_kcmd", "kubectl")
    ev["vllm_modelservice_gateway_class_name"] = ev.get(
        "vllm_modelservice_gateway_class_name", ""
    ).lower()

    ev["current_step_nr"] = ev["current_step"].split('_')[0]

def kubectl_apply(
    api: pykube.HTTPClient,
    manifest_data: Union[list, dict],
    dry_run: bool = False,
    verbose: bool = False,
):

    if not isinstance(manifest_data, dict):
        manifest_data = clear_string(manifest_data)
        manifest_data = yaml.safe_load(manifest_data)

    _pcc = __import__("pykube")
    object_kind = "N/A"
    object_name = "N/A"
    object_namespace = "NA"
    if not isinstance(manifest_data, list):
        manifest_items = []
        manifest_items.append(manifest_data)
    else :
        manifest_items = manifest_data

    for item in manifest_items:
        try :
            object_api = item["apiVersion"]
            object_kind = item["kind"]
            object_name = item["metadata"]["name"]
            object_namespace = item["metadata"]["namespace"]

            if dry_run:
                announce(f"[DRY RUN] Would have created/updated {object_kind} \"{object_name}\".")
                continue

            _pci = pykube.object_factory(api, object_api, object_kind)
            obj_instance = _pci(api, manifest_data)

            if obj_instance.exists():
                if object_kind != "Namespace" :
                    obj_instance = _pci.objects(api).filter(namespace=object_namespace).get_by_name(object_name)

                obj_instance.update()
                announce(f"🚀 Updated {object_kind} \"{object_name}\"")

            else:
                obj_instance.create()
                announce(f"🚀 Created {object_kind} \"{object_name}\"")

        except PyKubeError as e:
            announce(f"ERROR: Failed to create or update {object_kind} \"{object_name}\": {e}")
            sys.exit(1)

def kubectl_get(
    api: pykube.HTTPClient,
    object_api: str = '',
    object_kind: str = '',
    object_name: str = '',
    object_namespace: str = '',
    object_selector: dict = {},
    dry_run: bool = False,
    verbose: bool = False,
):
    _pcc = __import__("pykube")

    if dry_run:
        announce(f"[DRY RUN] Would have returned {object_kind}/{object_name}")
        return [],[]

    if object_api :
        _pci = pykube.object_factory(api, object_api, object_kind)
    else :
        _pci = getattr(_pcc, object_kind)

    object_instances = []
    object_names = []

    if object_name :
        if object_namespace :
            object_instances = _pci.objects(api).filter(namespace=object_namespace).get_by_name(object_name)
        else :
            object_instances = _pci.objects(api).get_by_name(object_name)
    elif object_selector :
        if object_namespace :
            object_instances = _pci.objects(api).filter(namespace=object_namespace, selector=object_selector)
        else :
            object_instances = _pci.objects(api).filter(selector=object_selector)
    else :
        if object_namespace :
            object_instances = _pci.objects(api).filter(namespace=object_namespace).all()
        else :
            object_instances = _pci.objects(api).all()

    if isinstance(object_instances, Query) :
        for i in object_instances :
            object_names.append(i.name)
    else :
        object_names = [ object_instances.name ]
        object_instances = [ object_instances ]

    return object_instances, object_names

def kubectl_delete(
    api: pykube.HTTPClient,
    object_api: str = '',
    object_kind: str = '',
    object_name: str = '',
    object_namespace: str = '',
    object_selector: dict = {},
    dry_run: bool = False,
    verbose: bool = False,
):
    _pcc = __import__("pykube")

    if dry_run:
        announce(f"[DRY RUN] Would have deleted {object_kind}/{object_name} on namespace {object_namespace}")
        return True

    if object_api :
        _pci = pykube.object_factory(api, object_api, object_kind)
    else :
        _pci = getattr(_pcc, object_kind)

    object_instances = []
    object_names = []

    try :
        if object_namespace :
            if object_selector :
                object_instances = _pci.objects(api).filter(namespace=object_namespace, selector=object_selector)
            else :
                object_instances = _pci.objects(api).filter(namespace=object_namespace).get_by_name(object_name)
        else :
            if object_selector :
                object_instances = _pci.objects(api).filter(selector=object_selector)
            else :
                object_instances = _pci.objects(api).get_by_name(object_name)

        if isinstance(object_instances, Query) :
            for i in object_instances :
                i.delete()
        else :
            object_instances.delete()

    except ObjectDoesNotExist as e :
        return True

    return True

def validate_and_create_pvc(
    api: pykube.HTTPClient,
    client: any,
    namespace: str,
    download_model: str,
    pvc_name: str,
    pvc_size: str,
    pvc_class: str,
    pvc_access_mode: str,
    dry_run: bool = False,
):
    announce("Provisioning model storage…")

    if download_model:
        if "/" not in download_model:
            announce(
                f"ERROR: '{download_model}' is not in Hugging Face format <org>/<repo>"
            )
            sys.exit(1)

    if not pvc_name:
        announce(f"ℹ️ Skipping pvc creation")
        return True

    announce(f"🔍 Checking storage class '{pvc_class}'...")
    try:
        storage_v1_api = client.StorageV1Api()

        if pvc_class == "default":
            for x in storage_v1_api.list_storage_class().items:
                if (
                    x.metadata.annotations
                    and "storageclass.kubernetes.io/is-default-class"
                    in x.metadata.annotations
                ):
                    if (
                        x.metadata.annotations[
                            "storageclass.kubernetes.io/is-default-class"
                        ]
                        == "true"
                    ):
                        announce(
                            f'ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS automatically set to "{x.metadata.name}"'
                        )
                        pvc_class = x.metadata.name
        storage_v1_api.read_storage_class(name=pvc_class)
        announce(f"ℹ️ StorageClass '{pvc_class}' found.")

    except k8s_client.ApiException as e:
        # if returns a 404 the storage class doesnt exist
        if e.status == 404:
            announce(f"ERROR: StorageClass '{pvc_class}' not found")
            sys.exit(1)
        else:
            # handle other
            announce(f"ERROR: unable to find StorageClass: {e}")
            sys.exit(1)
    except FileNotFoundError:
        announce("ERROR: Kubeconfig file not found. Cannot check StorageClass.")
        sys.exit(1)

    pvc_obj = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": namespace,
        },
        "spec": {
            "accessModes": [f"{pvc_access_mode}"],
            "resources": {"requests": {"storage": pvc_size}},
            "storageClassName": pvc_class,
            "volumeMode": "Filesystem",
        },
    }

    kubectl_apply(api=api, manifest_data=pvc_obj, dry_run=dry_run)

def launch_download_job(
    api: pykube.HTTPClient,
    ev: dict,
    download_model: str,
    model_path: str
):

    work_dir_str = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    current_step = os.getenv("LLMDBENCH_CURRENT_STEP", "step")
    kcmd =ev["control_kcmd"]

    work_dir = Path(work_dir_str)
    yaml_dir = work_dir / "setup" / "yamls"
    yaml_dir.mkdir(parents=True, exist_ok=True)
    yaml_file_path = yaml_dir / f"{current_step}_download_pod_job.yaml"

    announce("Launching model download job...")

    base_cmds = [
        'mkdir -p "${MOUNT_PATH}/${MODEL_PATH}"',
        "pip install huggingface_hub",
        'export PATH="${PATH}:${HOME}/.local/bin"',
    ]

    hf_cmds = []
    hf_token_env = ""
    models = ev["deploy_model_list"]
    for model_id in models.split(","):
        if is_hf_model_gated(model_id):
            if user_has_hf_model_access(model_id, ev["hf_token"]):
                #
                # Login is only required for GATED models.
                # https://huggingface.co/docs/hub/models-gated
                #
                hf_cmds.append('hf auth login --token "${HF_TOKEN}"')
                hf_token_env = f"""- name: HF_TOKEN
              valueFrom:
                secretKeyRef:
                  name: {ev["vllm_common_hf_token_name"]}
                  key: HF_TOKEN"""
            else:
                #
                # In theory - since we already check this in `env.sh` we shoudn't need to error
                # out here, we should really just be organizing the command for the yaml creation
                # but we haven't fully converted to python yet and for extra carefulness, lets just
                # check this here again since there may be some code path that some how gets here
                # without first sourcing env.sh and running the precheck there...
                #
                announce(
                    f"ERROR: Unauthorized access to gated model {model_path}. Check your HF Token."
                )
                sys.exit(1)
    hf_cmds.append('hf download "${HF_MODEL_ID}" --local-dir "/cache/${MODEL_PATH}"')
    base_cmds.extend(hf_cmds)
    command_args = " && ".join(base_cmds)

    job_name = "download-model"

    job_yaml = f"""
apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {ev["vllm_common_namespace"]}
spec:
  backoffLimit: 3
  template:
    metadata:
      namespace: {ev["vllm_common_namespace"]}
      labels:
        app: llm-d-benchmark-harness
    spec:
      containers:
        - name: downloader
          image: {get_image(ev["image_registry"], ev["image_repo"], ev["image_name"], ev["image_tag"], False, True)}
          command: ["/bin/sh", "-c"]
          args:
            - |
              {command_args}
          env:
            - name: MODEL_PATH
              value: {model_path}
            - name: HF_MODEL_ID
              value: {download_model}
            {hf_token_env}
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
            claimName: {ev["vllm_common_pvc_name"]}
"""

    with open(f'{ev["control_work_dir"]}/setup/yamls/{ev["current_step_nr"]}_download_job.yaml', "w") as f:
        f.write(job_yaml)

    announce(
        f"--> Deleting previous job '{job_name}' (if it exists) to prevent conflicts..."
    )
    kubectl_delete(api=api, object_kind='Job', object_name=job_name, object_namespace=ev['vllm_common_namespace'])
    kubectl_delete(api=api, object_kind='Pod', object_namespace=ev['vllm_common_namespace'], object_selector={'job-name': job_name})

    kubectl_apply(api=api, manifest_data=job_yaml, dry_run=ev["control_dry_run"])

async def wait_for_job(job_name, namespace, timeout=7200, dry_run: bool = False):
    """Wait for the  job to complete"""
    announce(f"Waiting for job {job_name} to complete...")

    if dry_run:
        announce(f"[DRY RUN] Evaluation job {job_name} completed successfully.")
        return True

    # use async config loading
    #TODO kube_connect(f'{ev["control_work_dir"]}/environment/context.ctx')
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
            timeout_seconds=timeout,  # replaces the manual timeout check
        ) as stream:

            async for (
                event
            ) in (
                stream
            ):  # replaces time.wait since we grab events as they come from stream sasynchronous
                job_status = event["object"].status
                if job_status.succeeded:
                    announce(f"Evaluation job {job_name} completed successfully.")
                    return True

                elif job_status.failed:
                    announce(f"Evaluation job {job_name} failed")
                    return False

    except asyncio.TimeoutError:
        announce(
            f"ERROR: Timeout waiting for evaluation job {job_name} after {timeout} seconds."
        )
        return False
    except Exception as e:
        announce(f"WARNING: (RECOVERABLE) Error occured while waiting for job {job_name} : {e}")
        return False
    finally:
        await api_client.close()

def model_attribute(model: str, attribute: str) -> str:

    if ":" in model:
        model, modelid = model.split(":", 1)
    else:
        modelid = model

    modelid = modelid.replace("/", "-").replace(".", "-")

    #  split the model name into provider and rest
    provider, model_part = model.split("/", 1) if "/" in model else ("", model)

    ns = os.getenv("LLMDBENCH_VLLM_COMMON_NAMESPACE")
    hash_object = hashlib.sha256()
    hash_object.update(f"{ns}/{modelid}".encode("utf-8"))
    digest = hash_object.hexdigest()
    modelid_label = f"{modelid[:8]}-{digest[:8]}-{modelid[-8:]}"

    # create a list of components from the model part
    # equiv  to: tr '[:upper:]' '[:lower:]' | sed -e 's^qwen^qwen-^g' -e 's^-^\n^g'
    model_components_str = model_part.lower().replace("qwen", "qwen-")
    model_components = model_components_str.split("-")

    # get individual attributes using regex
    type_str = "base"
    for comp in model_components:
        if re.search(r"nstruct|hf|chat|speech|vision|opt", comp, re.IGNORECASE):
            type_str = comp
            break

    parameters = ""
    for comp in model_components:
        if re.search(r"[0-9].*[bm]", comp, re.IGNORECASE):
            parameters = re.sub(r"^[a-z]", "", comp)
            parameters = parameters.split(".")[-1]

    major_version = "1"
    for comp in model_components:
        # find component that starts with a digit but is not the parameter string
        if comp.isdigit() or (
            comp and comp[0].isdigit() and not re.search(r"b|m", comp, re.IGNORECASE)
        ):
            # remove the parameter string from it if present ... for case like like "3.1-8B"
            version_part = comp.replace(parameters, "")
            major_version = version_part.split(".")[0]
            break

    kind = model_components[0] if model_components else ""

    as_label = model.lower().replace("/", "-").replace(".", "-")

    # build label and clean it up
    label_parts = [part for part in [kind, major_version, parameters] if part]
    label = "-".join(label_parts)
    label = re.sub(r"-+", "-", label).strip(
        "-"
    )  # replace multiple hyphens and strip from ends

    folder = model.lower().replace("/", "_").replace("-", "_")

    # storing all attributes in a dictionary
    attributes = {
        "model": model,
        "modelid": modelid,
        "modelcomponents": " ".join(model_components),
        "modelid_label": modelid_label,
        "provider": provider,
        "modeltype": type_str,
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

def extract_environment(ev):
    """
    Extract and display environment variables for debugging.
    Equivalent to the bash extract_environment function.
    """

    for key, value in os.environ.items():
        if "LLMDBENCH_" in key:
            ev[key.split("LLMDBENCH_")[1].lower()] = value

    # Get environment variables that start with LLMDBENCH, excluding sensitive ones
    env_vars = []
    for key, value in os.environ.items():
        if key.startswith("LLMDBENCH_") and not any(
            sensitive in key.upper()
            for sensitive in ["TOKEN", "USER", "PASSWORD", "EMAIL"]
        ):
            env_vars.append(f"{key}={value}")

    env_vars.sort()

    environment_variable_to_dict(ev)

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

def get_image(
    image_registry: str,
    image_repo: str,
    image_name: str,
    image_tag: str,
    tag_only: bool = False,
    silent: bool = False
) -> str:
    """
    Construct container image reference.
    Equivalent to the bash get_image function.

    Args:
        image_registry: Container registry
        image_repo: Repository/organization
        image_name: Image name
        image_tag: Image tag
        tag_only: If "True", return only the tag
        silent: If "True", do not output \"INFO\" message

    Returns:
        Full image reference or just tag
    """
    is_latest_tag = image_tag

    if image_tag == "auto":
        ccmd = os.getenv("LLMDBENCH_CONTROL_CCMD", "skopeo")
        image_full_name = f"{image_registry}/{image_repo}/{image_name}"

        if ccmd == "podman":
            # Use podman search to get latest tag
            cmd = f"{ccmd} search --list-tags --limit 1000 {image_full_name}"
            try:
                result = subprocess.run(
                    cmd.split(), capture_output=True, text=True, check=False
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split("\n")
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
                result = subprocess.run(
                    cmd.split(), capture_output=True, text=True, check=True
                )
                import json

                tags_data = json.loads(result.stdout)
                if tags_data.get("Tags"):
                    # Use jq -r .Tags[] | tail -1 equivalent
                    is_latest_tag = tags_data["Tags"][-1]
            except:
                is_latest_tag = ""

        if not is_latest_tag:
            announce(f'ERROR: Unable to find latest tag for image "{image_full_name}"')
            sys.exit(1)

        if not silent:
            announce(f"INFO: resolved image \"{image_full_name}:{image_tag}\" into \"{image_full_name}:{is_latest_tag}\"")

    if tag_only :
        return is_latest_tag
    else:
        return f"{image_registry}/{image_repo}/{image_name}:{is_latest_tag}"

def check_storage_class(ev):
    """
    Check and validate storage class configuration.
    Equivalent to the bash check_storage_class function.
    """
    try:
        # Use pykube to connect to Kubernetes
        control_work_dir = os.environ.get(
            "LLMDBENCH_CONTROL_WORK_DIR", "/tmp/llm-d-benchmark"
        )
        api, client = kube_connect(f"{control_work_dir}/environment/context.ctx")

        # Create StorageClass object - try pykube-ng first, fallback to custom class
        try:
            # Try pykube-ng's object_factory if available
            StorageClass = pykube.object_factory(
                api, "storage.k8s.io/v1", "StorageClass"
            )
        except AttributeError:
            # Fallback for older pykube versions - create custom StorageClass
            class StorageClass(pykube.objects.APIObject):
                version = "storage.k8s.io/v1"
                endpoint = "storageclasses"
                kind = "StorageClass"

        # Handle default storage class
        if ev["vllm_common_pvc_storage_class"] == "default":
            if ev["control_caller"] in ["standup.sh", "e2e.sh", "standup.py", "e2e.py"]:

                try:
                    # Find default storage class using pykube
                    storage_classes = StorageClass.objects(api)
                    default_sc = None

                    for sc in storage_classes:
                        annotations = sc.metadata.get("annotations", {})
                        if (
                            annotations.get(
                                "storageclass.kubernetes.io/is-default-class"
                            )
                            == "true"
                        ):
                            default_sc = sc.name
                            break

                    if default_sc:
                        announce(
                            f'ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS automatically set to "{default_sc}"'
                        )
                        os.environ["LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS"] = (
                            default_sc
                        )
                        ev["vllm_common_pvc_storage_class"] = default_sc

                    else:
                        announce(
                            f"ERROR: environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS=default, but unable to find a default storage class"
                        )
                        return False
                except Exception as e:
                    announce(f"ERROR: unable to find a \"default\" storage class: {e}")
                    return False

        # Verify storage class exists using pykube
        try:
            sc = StorageClass.objects(api).get(name=ev["vllm_common_pvc_storage_class"])
            if sc.exists():
                return True
            else:
                announce(
                    f"ERROR: Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS={ev['common_pvc_storage_class']} but could not find such storage class"
                )
                return False
        except pykube.exceptions.ObjectDoesNotExist:
            announce(
                f"ERROR: Environment variable LLMDBENCH_VLLM_COMMON_PVC_STORAGE_CLASS={ev['common_pvc_storage_class']} but could not find such storage class"
            )
            return False
        except Exception as e:
            announce(f"ERROR: checking storage class: {e}")
            return False

    except Exception as e:
        announce(f"ERROR: connecting to Kubernetes: {e}")
        return False

def check_affinity(ev: dict):
    """
    Check and validate affinity configuration.
    Equivalent to the bash check_affinity function.
    """
    caller = os.environ.get("LLMDBENCH_CONTROL_CALLER", "")
    if caller not in ["standup.sh", "e2e.sh", "standup.py", "e2e.py"]:
        return True

    affinity = os.environ.get("LLMDBENCH_VLLM_COMMON_AFFINITY", "")
    is_minikube = int(os.environ.get("LLMDBENCH_CONTROL_DEPLOY_IS_MINIKUBE", 0))

    try:
        # Use pykube to connect to Kubernetes
        control_work_dir = os.environ.get(
            "LLMDBENCH_CONTROL_WORK_DIR", "/tmp/llm-d-benchmark"
        )
        api, client = kube_connect(f"{control_work_dir}/environment/context.ctx")

        # Handle auto affinity detection
        if affinity == "auto":
            if (
                caller in ["standup.sh", "e2e.sh", "standup.py", "e2e.py"]
                and is_minikube == 0
            ):
                try:
                    # Get node labels to find accelerators using pykube
                    nodes = pykube.Node.objects(api)

                    accelerator_patterns = [
                        "nvidia.com/gpu.product",
                        "gpu.nvidia.com/class",
                        "cloud.google.com/gke-accelerator",
                    ]

                    found_accelerator = None
                    for node in nodes:
                        labels = node.metadata.get("labels", {})
                        for pattern in accelerator_patterns:
                            for label_key, label_value in labels.items():
                                if pattern in label_key:
                                    found_accelerator = f"{label_key}:{label_value}"
                                    break
                            if found_accelerator:
                                break
                        if found_accelerator:
                            break

                    if (
                        os.environ["LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE"]
                        == "auto"
                    ):
                        os.environ["LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE"] = (
                            "nvidia.com/gpu"
                        )

                    if found_accelerator:
                        os.environ["LLMDBENCH_VLLM_COMMON_AFFINITY"] = found_accelerator
                        announce(
                            f'ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_AFFINITY automatically set to "{found_accelerator}"'
                        )
                        os.environ["LLMDBENCH_VLLM_COMMON_AFFINITY"] = (
                            f"{found_accelerator}"
                        )

                        # Updates the common affinity env var if auto
                        ev["vllm_common_affinity"] = (
                            f"{os.environ.get('LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE')}:{found_accelerator}"
                        )
                    else:
                        announce(
                            "ERROR: environment variable LLMDBENCH_VLLM_COMMON_AFFINITY=auto, but unable to find an accelerator on any node"
                        )
                        return False
                except Exception as e:
                    announce(f"ERROR: unable to find nodes with requested affinity: {e}")
                    return False
        else:
            # Validate manually specified affinity using pykube
            if affinity and ":" in affinity:
                annotation_key, annotation_value = affinity.split(":", 1)
                try:
                    nodes = pykube.Node.objects(api)
                    found_matching_node = False

                    for node in nodes:
                        labels = node.metadata.get("labels", {})
                        if labels.get(annotation_key) == annotation_value:
                            found_matching_node = True
                            break

                    if not found_matching_node:
                        announce(
                            f'ERROR: There are no nodes on this cluster with the label "{annotation_key}:{annotation_value}" (environment variable LLMDBENCH_VLLM_COMMON_AFFINITY)'
                        )
                        return False
                except Exception as e:
                    announce(f"ERROR: unable to validate affinity: {e}")
                    return False

        # Handle auto accelerator resource detection
        accelerator_resource = os.environ.get(
            "LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE", ""
        )
        if accelerator_resource == "auto":
            os.environ["LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE"] = "nvidia.com/gpu"
            announce(
                f'ℹ️ Environment variable LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE automatically set to "nvidia.com/gpu"'
            )

        return True

    except Exception as e:
        announce(f"ERROR: unable to connect to cluster: {e}")
        return False

def get_accelerator_nr(accelerator_nr, tp, dp) -> int:
    """
    Get the number of accelerator resources needed.
    Equivalent to the Bash get_accelerator_nr function.
    """

    # Calculate number of accelerators needed
    needed_accelerators = int(tp) * int(dp)

    if accelerator_nr != "auto":
        return accelerator_nr
    else :
        return needed_accelerators

def add_annotations(ev: dict, varname: str) -> str:
    """
    Generate pod annotations YAML.
    Equivalent to the bash add_annotations function.
    """
    varname = varname.replace("LLMDBENCH_",'',1).lower()
    annotations = ev[varname].replace("auto",'')
    if not annotations:
        return ""

    annotations = ev[varname].replace("auto",'')

    if ev["control_environment_type_standalone_active"] :
        indent = "        "  # 8 spaces
    elif ev["control_environment_type_modelservice_active"] :
        indent = "      "  # 6 spaces
    else:
        indent = "        "  # default 8 spaces

    # Parse annotations (comma-separated key:value pairs)
    if not annotations.count("stood-up-by:") :
        annotations = f"{annotations},stood-up-by:{ev['control_username']}"

    if not annotations.count("stood-up-from:") :
        annotations = f"{annotations},stood-up-from:llm-d-benchmark"

    if not annotations.count("stood-up-via:") :
        annotations = f"{annotations},stood-up-via:{ev['deploy_methods']}"

    annotation_lines = []
    for entry in annotations.split(","):
        if ":" in entry:
            key, value = entry.split(":", 1)
            annotation_lines.append(f'{indent}{key.strip()}: "{value.strip()}"')

    return "\n".join(annotation_lines)

def render_string(input_string):
    """
    Process REPLACE_ENV variables in a string, equivalent to bash render_string function.

    Args:
        input_string: String that may contain REPLACE_ENV_VARIABLE_NAME placeholders

    Returns:
        String with REPLACE_ENV placeholders substituted with actual environment variable values
    """
    if not input_string:
        return ""

    # Find all REPLACE_ENV entries
    # Pattern matches: REPLACE_ENV_VARIABLE_NAME or REPLACE_ENV_VARIABLE_NAME++++default=value
    import re

    # Split string on various delimiters to find REPLACE_ENV tokens
    # Equivalent to: echo ${string} | sed -e 's/____/ /g' -e 's^-^\n^g' -e 's^:^\n^g' -e 's^/^\n^g' -e 's^ ^\n^g' -e 's^]^\n^g' -e 's^ ^^g' | grep -E "REPLACE_ENV" | uniq
    working_string = input_string.replace("____", " ")

    # Find REPLACE_ENV patterns
    replace_env_pattern = r'REPLACE_ENV_[A-Z0-9_]+(?:\+\+\+\+default=[^"\s]*)?'
    matches = re.findall(replace_env_pattern, working_string)

    # Process each REPLACE_ENV match
    processed_string = input_string.replace("{\\n}", "\n").replace("{\\s}", " ").replace("____"," ")
    for match in set(matches):  # Use set to get unique matches
        # Extract parameter name and default value
        if "++++default=" in match:
            env_part, default_part = match.split("++++default=", 1)
            parameter_name = env_part.replace("REPLACE_ENV_", "")
            default_value = default_part
        else:
            parameter_name = match.replace("REPLACE_ENV_", "")
            default_value = ""

        # Get environment variable value
        env_value = os.environ.get(parameter_name, "")

        # Determine final value
        if env_value:
            final_value = env_value
        elif default_value:
            final_value = default_value
        else:
            announce(f'ERROR: variable "REPLACE_ENV_{parameter_name}" not defined!')
            sys.exit(1)

        # Replace in the string
        processed_string = processed_string.replace(match, final_value)

        processed_string = clear_string(processed_string)

    return processed_string

def add_command(model_command: str) -> str:
    """
    Generate command section for container based on model_command type.
    """
    if model_command == "custom":
        return """command:
      - /bin/sh
      - '-c'"""
    return ""

def add_command_line_options(ev: dict, args_string: str) -> str:
    """
    Generate command line options for container args.
    In case args_string is a file path, open the file and read the contents first
    Equivalent to the bash add_command_line_options function.
    """
    current_step = os.environ["LLMDBENCH_CURRENT_STEP"].split("_")[0]

    if os.access(args_string, os.R_OK):
        with open(args_string, "r") as fp:
            fc = fp.read()
        args_string = fc

    # Process REPLACE_ENV variables first
    if args_string:
        processed_args = render_string(args_string)

        # Handle formatting based on step and content
        if current_step == "06":
            # For step 06 (standalone), format as YAML list item with proper spacing
            if "[" in processed_args and "]" in processed_args:
                # Handle array format: convert [arg1____arg2____arg3] to proper format
                processed_args = processed_args.replace("[", "").replace("]", "")
                processed_args = processed_args.replace("____", " ")
                # Add proper line breaks and indentation for multi-line args
                processed_args = processed_args.replace(" --", " \\\n            --")
            else:
                # Handle regular string format: convert ____;____arg1____arg2
                processed_args = processed_args.replace("____", " ")
                # Only replace the first semicolon with newline, leave others as-is
                processed_args = processed_args.replace(";", ";\n          ", 1)
                processed_args = processed_args.replace(" --", " \\\n            --")

            processed_args = clear_string(processed_args)

            if ev["vllm_common_enable_sleep_mode"] :
                processed_args = processed_args.split('\n')

                processed_args[-1] = f"{processed_args[-1]} \\\n            --enable-sleep-mode"
                processed_args = '\n'.join(processed_args)

            return f"        - |\n          {processed_args}"
        elif current_step == "09":
            # For step 09 (modelservice), format as proper YAML list
            if "[" in processed_args and "]" in processed_args:
                # Handle array format with potential complex arguments
                processed_args = processed_args.replace("[", "").replace("]", "")

                args_list = []
                for arg in processed_args.split("--") :
                    if arg :
                        if arg.count(' ') :
                            args_list.append(f"--{arg.split(' ')[0]}")
                            args_list.append(f"{arg.split(' ')[1]}")
                        else :
                            args_list.append(f"--{arg}")

                # Create proper YAML list items with escaped quotes
                yaml_list = []
                for arg in args_list:
                    if arg.strip():
                        # Clean up any trailing artifacts from line continuation
                        cleaned_arg = arg.rstrip("\\").rstrip('"').strip()
                        if cleaned_arg:
                            # Handle JSON strings and complex arguments with proper quoting
                            if cleaned_arg.startswith("'") and cleaned_arg.endswith(
                                "'"
                            ):
                                # Already has single quotes - use as-is for JSON strings
                                yaml_list.append(f"      - {cleaned_arg}")
                            else:
                                # Regular argument - wrap in double quotes
                                yaml_list.append(f'      - "{cleaned_arg}"')

                #TODO             if ev["vllm_common_enable_sleep_mode"] :

                return "\n".join(yaml_list)
            else:
                processed_args = f"{processed_args.replace('____', ' ')}"
                args_list = processed_args.split("--")
                cmd_param_list = ["- |"]
                for arg in args_list:
                    cmd_param_list.append(f"        --{arg.strip()} \\")

                cmd_param_list[-1] = cmd_param_list[-1].replace("\\", "")
                cmd_string = "\n".join(cmd_param_list).replace("--", "", 1)

                #TODO             if ev["vllm_common_enable_sleep_mode"] :

                return cmd_string
        else:
            # Default case
            processed_args = processed_args.replace("____", " ")
            return processed_args
    else:
        # Handle empty args_string
        if current_step == "06":
            return "        - |"
        else:
            return ""

def add_resources(ev:dict, identifier: str) -> [str, str]:

    limits_resources = []
    requests_resources = []

    if ev["control_environment_type_standalone_active"] :
        identifier = "common"
        section_indent = " " * 12

    if ev["control_environment_type_modelservice_active"] :
        identifier = f"modelservice_{identifier}"
        section_indent = " " * 8

    if ev["control_environment_type_standalone_active"] :
        accelerator_resource = ev[f"vllm_{identifier}_accelerator_resource"]

        if accelerator_resource == "auto":
            accelerator_resource = "nvidia.com/gpu"

        accelerator_nr = ev[f"vllm_{identifier}_accelerator_nr"]

        data_local_parallelism = ev[f"vllm_{identifier}_data_local_parallelism"]
        tensor_parallelism = ev[f"vllm_{identifier}_tensor_parallelism"]

        accelerator_count = get_accelerator_nr(
            accelerator_nr, tensor_parallelism, data_local_parallelism
        )

    cpu_mem = ev[f"vllm_{identifier}_cpu_mem"]
    cpu_nr = ev[f"vllm_{identifier}_cpu_nr"]

    ephemeral_storage_resource = ev.get("vllm_common_ephemeral_storage_resource", "")
    ephemeral_storage_nr = ev[f"vllm_{identifier}_ephemeral_storage_nr"]

    decode_network_resource = ev.get("vllm_modelservice_decode_network_resource", "")
    decode_network_nr = ev.get("vllm_modelservice_decode_network_nr", "")

    network_resource = ev[f"vllm_{identifier}_network_resource"]
    network_nr = ev[f"vllm_{identifier}_network_nr"]

    if cpu_mem:
        limits_resources.append(f"{section_indent}memory: {cpu_mem}")
        requests_resources.append(f"{section_indent}memory: {cpu_mem}")
    if cpu_nr:
        limits_resources.append(f'{section_indent}cpu: "{cpu_nr}"')
        requests_resources.append(f'{section_indent}cpu: "{cpu_nr}"')
    if ephemeral_storage_resource and ephemeral_storage_nr:
        limits_resources.append(
            f'{section_indent}{ephemeral_storage_resource}: "{ephemeral_storage_nr}"'
        )
        requests_resources.append(
            f'{section_indent}{ephemeral_storage_resource}: "{ephemeral_storage_nr}"'
        )

    if ev["control_environment_type_standalone_active"] :
        if (
            accelerator_resource
            and accelerator_count
            and str(accelerator_count) != "0"
        ):
            limits_resources.append(
                f'{section_indent}{accelerator_resource}: "{accelerator_count}"'
            )
            requests_resources.append(
                f'{section_indent}{accelerator_resource}: "{accelerator_count}"'
            )

    if network_resource and network_nr:
        limits_resources.append(
            f'{section_indent}{network_resource}: "{network_nr}"'
        )
        requests_resources.append(
            f'{section_indent}{network_resource}: "{network_nr}"'
        )

    if limits_resources :
        limits_resources = "\n".join(limits_resources)
    else :
        limits_resources = f"{section_indent}'{''}'"

    if requests_resources :
        requests_resources = "\n".join(requests_resources)
    else :
        requests_resources = f"{section_indent}'{''}'"

    return limits_resources, requests_resources

def add_affinity(ev:dict) -> str:

    affinity = ev["vllm_common_affinity"]
    if ":" in affinity:
        affinity_key, affinity_value = affinity.split(":", 1)
    else:
        affinity_key, affinity_value = "", ""

    if ev["control_environment_type_standalone_active"] :
        affinity_string = f"""      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
            - matchExpressions:
              - key: {affinity_key}
                operator: In
                values:
                - {affinity_value}"""

    if ev["control_environment_type_modelservice_active"] :

        if affinity_key == "kubernetes.io/os" :
            return ""

        affinity_string = f"""  acceleratorTypes:
    labelKey: {affinity_key}
    labelValues:
    - {affinity_value}"""

    return affinity_string

def add_accelerator(ev:dict, identifier: str = "decode") -> str:

    if ev[f"vllm_modelservice_{identifier}_accelerator_resource"] == "auto" :
        ev[f"vllm_modelservice_{identifier}_accelerator_resource"] = ev[f"vllm_common_affinity"].split(':')[0].replace(".product",'')

    accelerator_type = ev[f"vllm_modelservice_{identifier}_accelerator_resource"].split('.')[0]
    if accelerator_type == "kubernetes" :
        accelerator_type = "cpu"
        acellerator_resource = "cpu"

    acellerator_resource = ev[f"vllm_modelservice_{identifier}_accelerator_resource"]
    accelerator_string=f"""accelerator:
  type: {accelerator_type}
    """
    # rely on hardcoded list (in modelservice) for these resources
    if accelerator_type not in ['nvidia', 'intel-i915', 'intel-xe', 'intel-gaudi', 'amd', 'google']:
        accelerator_string = f"""{accelerator_string}
  resources:
    {accelerator_type}: "{acellerator_resource}"
    """
    return accelerator_string

def add_pull_secret(ev:dict) -> str:
    pull_secret_string = "#noconfig"
    if ev["vllm_common_pull_secret"] :
        pull_secret_string = f"""      imagePullSecrets:
      - name: {ev["vllm_common_pull_secret"]}"""

    pull_secret_string = clear_string(pull_secret_string)

    return pull_secret_string

def add_additional_env_to_yaml(ev: dict, env_vars_string: str) -> str:
    """
    Generate additional environment variables YAML.
    In case env_vars_string is a file path, open the file and read the contents first
    Equivalent to the bash add_additional_env_to_yaml function.

    Args:
        env_vars_string (str): Comma separated list of environment variable
            names to be converted to name/value pairs OR a path to a file
            containing a YAML snippet to be indented but otherwise not
            interpreted.

    Returns:
        str: YAML snippet to be inserted to YAML template.
    """

    # Determine indentation based on environment type
    if ev["control_environment_type_standalone_active"] :
        name_indent = " " * 8
        value_indent = " " * 10
    elif ev["control_environment_type_modelservice_active"] :
        name_indent = " " * 6
        value_indent = " " * 8
    else:
        name_indent = " " * 8
        value_indent = " " * 10

    if os.access(env_vars_string, os.R_OK):
        lines = []
        with open(env_vars_string, "r") as fp:
            for line in fp:
                if line[0] != "#":
                    line = render_string(line)
                    lines.append(name_indent + line.rstrip())
        return "\n".join(lines)

    # Parse environment variables (comma-separated list)
    env_lines = []
    for envvar in env_vars_string.split(","):
        envvar = envvar.strip()
        if envvar:
            # Remove LLMDBENCH_VLLM_STANDALONE_ prefix if present
            clean_name = envvar
            if envvar[0] == "_":
                clean_name = envvar[1:]
            clean_name = clean_name.replace("LLMDBENCH_VLLM_COMMON_VLLM_", "VLLM_")
            clean_name = clean_name.replace("LLMDBENCH_VLLM_STANDALONE_VLLM_", "VLLM_")
            clean_name = clean_name.replace("LLMDBENCH_VLLM_MODELSERVICE_PREFILL_VLLM_", "VLLM_")
            clean_name = clean_name.replace("LLMDBENCH_VLLM_MODELSERVICE_DECODE_VLLM_", "VLLM_")
            clean_name = clean_name.replace("LLMDBENCH_VLLM_STANDALONE_", "")
            clean_name = clean_name.replace("LLMDBENCH_VLLM_COMMON_VLLM_", "VLLM_")
            env_value = os.environ.get(envvar, "")

            # Process REPLACE_ENV variables in the value (equivalent to bash sed processing)
            if env_value:
                processed_value = render_string(env_value)
            else:
                processed_value = ""

            env_lines.append(f"{name_indent}- name: {clean_name}")
            env_lines.append(f'{value_indent}value: "{processed_value}"')

    return "\n".join(env_lines)

def add_config(obj_or_filename, num_spaces=0, label=""):
    spaces = " " * num_spaces
    contents = ""
    indented_contents = ""

    contents = obj_or_filename

    if len(obj_or_filename.split("\n")) == 1:
        try:
            with open(obj_or_filename, "r") as f:
                contents = f.read()
        except FileNotFoundError:
            pass

    contents = render_string(contents)

    indented_contents = "\n".join(f"{spaces}{line}" for line in contents.splitlines())
    if indented_contents.strip() not in ["{}", "[]"]:
        indented_contents = f"  {label}\n{indented_contents}"
    else:
        indented_contents = ""
    return indented_contents

def is_standalone_deployment(ev: dict) -> bool:
    """
    Returns true if it is a standalone deployment
    """
    return ev["control_environment_type_standalone_active"]

def get_accelerator_type(ev: dict) -> str | None:
    """
    Attempts to get the GPU type
    """

    common_affinity = ev["vllm_common_affinity"]
    if common_affinity == "auto":
        return common_affinity
    else:
        # Parse the string
        # LLMDBENCH_VLLM_COMMON_AFFINITY=nvidia.com/gpu.product:NVIDIA-H100-80GB-HBM3
        parsed = common_affinity.split(":")
        return parsed[-1]

def is_hf_model_gated(model_id: str) -> bool:
    """
    Check if a HF model is gated,
    meaning it requires manual approval
    before a user can access it.

    Gated models require the user to authenticate with a valid Hugging Face token
    that has been granted access to use the model.

    Args:
        model_id (str): The model identifier within the repository, e.g., "ibm-granite/granite-3.1-8b-instruct".

    Returns:
        bool: True if the model is gated and requires manual approval, False otherwise.

    Notes:
        If the request to the Hugging Face API fails for any reason, the function
        will print the error and return False.

    Usage:
        >> is_hf_model_gated("ibm-granite/granite-3.1-8b-instruct")
        True
    """
    url = f"https://huggingface.co/api/models/{model_id}"
    try:
        headers = {"Accept": "application/json"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("gated", False) != False
    except requests.RequestException as e:
        announce(f"ERROR: HF request failed: {e}")
        return False

def user_has_hf_model_access(model_id: str, hf_token: str) -> bool:
    """
    Check if a Hugging Face user (identified by hf_token) has access to a model.

    This is done by attempting to access a common file (config.json) in the
    model repository. If the file can be retrieved successfully, the user has access.

    Args:
        model_id (str): The model identifier within the repository, e.g., "ibm-granite/granite-3.1-8b-instruct".
        hf_token (str): Hugging Face API token with user authentication.

    Returns:
        bool: True if the user has access to the model, False if access is denied
              or if the request fails.

    Notes:
        - The function checks access to `config.json` as a proxy for model access.
        - Status codes 401 (Unauthorized) or 403 (Forbidden) are treated as no access.
        - Other exceptions during the request will print an error and return False.

    Usage:
        >> user_has_hf_model_access("ibm-granite/granite-3.1-8b-instruct", "<YOUR_HF_TOKEN>")
        True
    """
    url = f"https://huggingface.co/{model_id}/resolve/main/config.json"
    headers = {"Authorization": f"Bearer {hf_token}"}

    try:
        with requests.get(
            url, headers=headers, allow_redirects=True, stream=True
        ) as response:
            if response.status_code == 200:
                return True
            elif response.status_code in (401, 403):
                return False
            else:
                response.raise_for_status()
    except requests.RequestException as e:
        announce(f"ERROR: HF request failed: {e}")
        return False

def get_rand_string(length: int = 8):
    """
    Generate a random string with lower case characters and digits
    """

    characters = string.ascii_lowercase + string.digits
    random_string = "".join(random.choices(characters, k=length))
    return random_string

def get_model_name_from_pod(api: pykube.HTTPClient,
                            client: any,
                            namespace: str,
                            image: str,
                            ip: str,
                            port: str):
    """
    Get model name by starting/running a pod
    """

    if not ip :
        return "empty", "N/A"

    pod_name = f"testinference-pod-{get_rand_string()}"
    if "http://" not in ip:
        ip = "http://" + ip
    if ip.count(":") == 1:
        ip = ip + ":" + port
    ip = ip + "/v1/models"
    curl_command = f"curl --no-progress-meter {ip}"
    full_command = ["/bin/bash", "-c", f"curl --no-progress-meter {ip}"]
    pod_manifest = client.V1Pod(
        metadata=client.V1ObjectMeta(name=pod_name, namespace=namespace),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[
                client.V1Container(name="model", image=image, command=full_command)
            ],
        ),
    )

    api_instance = client.CoreV1Api()
    api_instance.create_namespaced_pod(namespace=namespace, body=pod_manifest)

    while True:
        pod_status = api_instance.read_namespaced_pod_status(pod_name, namespace)
        if pod_status.status.phase != "Pending":
            break
        time.sleep(1)

    pod_logs = api_instance.read_namespaced_pod_log(
        name=pod_name, namespace=namespace, tail_lines=100
    )

    model_name = "empty"
    if pod_logs:
        if pod_logs.count("'id': '"):
            model_name = pod_logs.split("'id': '")[1].split("', '")[0]
        else:
            model_name = "malformed"
    # Clean up
    api_instance.delete_namespaced_pod(
        name=pod_name,
        namespace=namespace,
        body=k8s_client.V1DeleteOptions(
            propagation_policy="Foreground", grace_period_seconds=10
        ),
    )
    return model_name, curl_command

def add_scc_to_service_account(
    api: pykube.HTTPClient,
    scc_name: str,
    service_account_name: str,
    namespace: str,
    dry_run: bool,
):
    announce(
        f'Attempting to add SCC "{scc_name}" to Service Account "{service_account_name}" in namespace "{namespace}"...'
    )

    try:
        # get the specified SecurityContextConstraints object
        scc = SecurityContextConstraints.objects(api).get(name=scc_name)
    except PyKubeError as e:
        if e.code == 404:
            announce(f'WARNING: SCC "{scc_name}" not found. Skipping.')
            return
        else:
            # re raise other API errors
            raise e

    # the username for a service account in scc is in the format:
    # system:serviceaccount:<namespace>:<service_account_name>
    sa_user_name = f"system:serviceaccount:{namespace}:{service_account_name}"

    # ensure the users field exists in the scc object it might be None or not present
    if "users" not in scc.obj or scc.obj["users"] is None:
        scc.obj["users"] = []

    # check if the service account is already in the list
    if sa_user_name in scc.obj["users"]:
        announce(
            f'Service Account "{sa_user_name}" already has SCC "{scc_name}". No changes needed'
        )
    else:
        if dry_run:
            announce(f'DRY RUN: Would add "{sa_user_name}" to SCC "{scc_name}"')
        else:
            announce(f'Adding "{sa_user_name}" to SCC "{scc_name}"...')
            scc.obj["users"].append(sa_user_name)
            scc.update()
            announce(f'Successfully updated SCC "{scc_name}"')


def wait_for_pods_created_running_ready(api_client, ev: dict, component_nr: int, component: str) -> int:
    """
    Wait for pods to be created, in Running state and then in Ready state.
    """

    dry_run = int(ev.get("control_dry_run", 0))
    result = 0
    ev["control_wait_timeout"] = int(ev["control_wait_timeout"])
    if not dry_run and int(component_nr) > 0:
        delay = 10
        max_retries = int(ev["control_wait_timeout"]/delay)
        announce(
            f'⏳ Waiting for all ({component}) pods serving model to be in "Running" state (timeout={ev["control_wait_timeout"]}s/{max_retries} tries)...'
        )
        pod_create_list = []
        for attempt in range(max_retries):
            try:
                w = k8s_watch.Watch()
                pod_running_list = []
                pod_ready_list = []
                for event in w.stream(api_client.list_namespaced_pod, namespace=ev["vllm_common_namespace"], label_selector=f"llm-d.ai/model={ev['deploy_current_model_id_label']},llm-d.ai/role={component}", timeout_seconds=int(ev["control_wait_timeout"])):
                    pod = event['object']
                    event_type = event['type']
                    if event_type in ("ADDED", "MODIFIED") and (pod.status.init_container_statuses or pod.status.container_statuses):
                        if pod.status.init_container_statuses and (len(pod_running_list) < int(component_nr)):
                            for init_container_status in pod.status.init_container_statuses:
                                if init_container_status.state and init_container_status.state.waiting and init_container_status.state.waiting.reason == "CrashLoopBackOff":
                                    announce(f"ERROR: init:CrashLoopBackOff in pod: {pod.metadata.name}, container: {container_status.name}")
                                    result = 1
                                    return result
                                elif init_container_status.state.terminated and init_container_status.state.terminated.exit_code not in (0, None):
                                    announce(f"ERROR: Crashed init:container in pod: {pod.metadata.name}, container: {container_status.name}")
                                    result = 2
                                    return result
                        if pod.status.container_statuses:
                            if pod.metadata.name not in pod_create_list:
                                announce(f"✅     \"{pod.metadata.name}\" ({component}) pod serving model created")
                                pod_create_list.append(pod.metadata.name)
                            for container_status in pod.status.container_statuses:
                                if container_status.state.waiting and container_status.state.waiting.reason == "CrashLoopBackOff":
                                    announce(f"ERROR: CrashLoopBackOff in pod: {pod.metadata.name}, container: {container_status.name}")
                                    result = 3
                                    return result
                                elif container_status.state.terminated and container_status.state.terminated.exit_code not in (0, None):
                                    announce(f"ERROR: Crashed container in pod: {pod.metadata.name}, container: {container_status.name}")
                                    result = 4
                                    return result
                            if pod.metadata.name not in pod_running_list and all(cs.state.running for cs in pod.status.container_statuses):
                                announce(f"🚀     \"{pod.metadata.name}\" ({component}) pod serving model running")
                                announce(f"⏳ Waiting for all ({component}) pods to be Ready (timeout={int(ev['control_wait_timeout'])}s)...")
                                pod_running_list.append(pod.metadata.name)
                            if pod.metadata.name not in pod_ready_list and all(cs.ready for cs in pod.status.container_statuses):
                                announce(f"🚀     \"{pod.metadata.name}\" ({component}) pod serving model ready")
                                pod_ready_list.append(pod.metadata.name)
                                if len(pod_create_list) == len(pod_ready_list) and len(pod_ready_list) == int(component_nr):
                                    result = 0
                                    return result
            except (Exception, ProtocolError) as e:
                if "Response ended prematurely" in str(e):
                    announce(f"WARNING: {e}, NOT-FATAL, retrying in {delay} seconds...")
                    time.sleep(delay)
                else:
                    announce(f"ERROR: Exception occured while waiting for ({component}) pods : {e}")
                    result = 5
                    return result
            finally:
                w.stop()
    return result

# FIXME (USE PYKUBE)
def collect_logs(ev: dict, component_nr: int, component: str) -> int:
    """
    Collect logs from component pods.
    """
    if component_nr == 0:
        return ""

    # Create logs directory
    logs_dir = Path(ev["control_work_dir"]) / "setup" / "logs"
    if not ev["control_dry_run"]:
        logs_dir.mkdir(parents=True, exist_ok=True)

    # Collect logs
    log_file = logs_dir / f"llm-d-{component}.log"
    log_cmd = f"kubectl --namespace {ev['vllm_common_namespace']} logs --tail=-1 --prefix=true -l llm-d.ai/model={ev['deploy_current_model_id_label']},llm-d.ai/role={component} > {log_file}"
    return llmdbench_execute_cmd(log_cmd, ev["control_dry_run"], ev["control_verbose"])

# ----------------------- Capacity Planner Sanity Check -----------------------
COMMON = "COMMON"
PREFILL = "PREFILL"
DECODE = "DECODE"


@dataclass
class ValidationParam:
    models: List[str]
    hf_token: str
    replicas: int
    gpu_type: str
    gpu_memory: int
    tp: int
    dp: int
    accelerator_nr: int
    requested_accelerator_nr: int
    gpu_memory_util: float
    max_model_len: int


def convert_accelerator_memory(gpu_name: str, accelerator_memory_param: str) -> int:
    """
    Try to guess the accelerator memory from its name
    """

    try:
        return int(accelerator_memory_param)
    except ValueError:
        # String is not an integer
        pass

    result = 0

    if gpu_name == "auto":
        announce(
            f"⚠️ Accelerator (LLMDBENCH_VLLM_COMMON_AFFINITY) type is set to be automatically detected, but requires connecting to kube client. The affinity check is invoked at a later step. To exercise the capacity planner, set LLMDBENCH_COMMON_ACCELERATOR_MEMORY. Otherwise, capacity planner will use 0 as the GPU memory."
        )

    match = re.search(r"(\d+)\s*GB", gpu_name, re.IGNORECASE)
    if match:
        result = int(match.group(1))
    else:
        # Some names might use just a number without GB (e.g., H100-80)
        match2 = re.search(r"-(\d+)\b", gpu_name)
        if match2:
            result = int(match2.group(1))

    if result > 0:
        announce(
            f"Determined GPU memory={result} from the accelerator's name: {gpu_name}. It may be incorrect, please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY for accuracy."
        )

    return result


def get_model_info(
    model_name: str, hf_token: str, ignore_if_failed: bool
) -> ModelInfo | None:
    """
    Obtains model info from HF
    """

    if ignore_if_failed:
        msgtag = "WARNING:"
    else:
        msgtag = "ERROR:"

    try:
        return get_model_info_from_hf(model_name, hf_token)

    except GatedRepoError:
        announce(
            f"{msgtag} Model is gated and the token provided via LLMDBENCH_HF_TOKEN does not, work. Please double check."
        )
    except HfHubHTTPError as hf_exp:
        announce(
            f"{msgtag} unable to connect to Hugging Face API gateway: Is LLMDBENCH_HF_TOKEN correctly set? {hf_exp}"
        )
    except Exception as e:
        announce(f"{msgtag} Cannot retrieve ModelInfo: {e}")

    return None


def get_model_config_and_text_config(
    model_name: str, hf_token: str, ignore_if_failed: bool
) -> Tuple[AutoConfig | None, AutoConfig | None]:
    """
    Obtains model config and text config from HF
    """

    if ignore_if_failed:
        msgtag = "WARNING:"
    else:
        msgtag = "ERROR:"

    try:
        config = get_model_config_from_hf(model_name, hf_token)
        return config, get_text_config(config)

    except GatedRepoError:
        announce(
            f"{msgtag} Model is gated and the token provided via LLMDBENCH_HF_TOKEN does not work. Please double check."
        )
    except HfHubHTTPError as hf_exp:
        announce(
            f"{msgtag} unable to connect to Hugging Face API gateway. Is LLMDBENCH_HF_TOKEN correctly set? {hf_exp}"
        )
    except Exception as e:
        announce(f"{msgtag} Cannot retrieve model config: {e}")

    return None, None


def validate_vllm_params(
    param: ValidationParam, ignore_if_failed: bool, type: str = COMMON
):
    """
    Given a list of vLLM parameters, validate using capacity planner
    """

    if ignore_if_failed:
        msgtag = "WARNING:"
    else:
        msgtag = "ERROR:"

    env_var_prefix = COMMON
    if type != COMMON:
        env_var_prefix = f"MODELSERVICE_{type}"

    models_list = param.models
    hf_token = param.hf_token
    replicas = param.replicas
    gpu_memory = param.gpu_memory
    tp = param.tp
    dp = param.dp
    user_requested_gpu_count = int(param.requested_accelerator_nr)
    max_model_len = param.max_model_len
    gpu_memory_util = param.gpu_memory_util

    # Sanity check on user inputs. If GPU memory cannot be determined, return False indicating that the sanity check is incomplete
    skip_gpu_tests = False
    if gpu_memory is None or gpu_memory == 0:
        announce(
            f"{msgtag} Cannot determine accelerator memory. Please set LLMDBENCH_VLLM_COMMON_ACCELERATOR_MEMORY to enable Capacity Planner. Skipping GPU memory required checks, especially KV cache estimation."
        )
        skip_gpu_tests = True

    per_replica_requirement = gpus_required(tp=tp, dp=dp)
    if replicas == 0:
        per_replica_requirement = 0
    total_gpu_requirement = per_replica_requirement

    if total_gpu_requirement > user_requested_gpu_count:
        announce(
            f"{msgtag} Accelerator requested is {user_requested_gpu_count} but it is not enough to stand up the model. Set LLMDBENCH_VLLM_{env_var_prefix}_ACCELERATOR_NR to TP x DP = {tp} x {dp} = {total_gpu_requirement}"
        )

    if total_gpu_requirement < user_requested_gpu_count:
        announce(
            f"{msgtag} For each replica, model requires {total_gpu_requirement}, but you requested {user_requested_gpu_count} for the deployment. Note that some GPUs will be idle."
        )

    # Use capacity planner for further validation
    for model in models_list:
        model_info = get_model_info(model, hf_token, ignore_if_failed)
        model_config, text_config = get_model_config_and_text_config(
            model, hf_token, ignore_if_failed
        )

        if model_config is not None:
            # Check if parallelism selections are valid
            try:
                valid_tp_values = find_possible_tp(text_config)
                if tp not in valid_tp_values:
                    announce(
                        f"{msgtag} TP={tp} is invalid. Please select from these options ({valid_tp_values}) for {model}."
                    )
            except AttributeError:
                # Error: config['num_attention_heads'] not in config
                announce(
                    f"{msgtag} Cannot obtain data on the number of attention heads, cannot find valid tp values: {e}"
                )

            # Check if model context length is valid
            valid_max_context_len = 0
            try:
                # Error: config['max_positional_embeddings'] not in config
                valid_max_context_len = max_context_len(model_config)
            except AttributeError as e:
                announce(
                    f"{msgtag} Cannot obtain data on the max context length for model: {e}"
                )

            if max_model_len > valid_max_context_len:
                announce(
                    f"{msgtag}  Max model length = {max_model_len} exceeds the acceptable for {model}. Set LLMDBENCH_VLLM_COMMON_MAX_MODEL_LEN to a value below or equal to {valid_max_context_len}"
                )
        else:
            announce(f"{msgtag} Model config on parameter shape not available.")

        # Display memory info
        if not skip_gpu_tests:
            announce("👉 Collecting GPU information....")
            avail_gpu_memory = available_gpu_memory(gpu_memory, gpu_memory_util)
            announce(
                f"ℹ️ {gpu_memory} GB of memory per GPU, with {gpu_memory} GB x {gpu_memory_util} (gpu_memory_utilization) = {avail_gpu_memory} GB available to use."
            )
            announce(
                f"ℹ️ Each model replica requires {per_replica_requirement} GPUs, total available GPU memory = {avail_gpu_memory * per_replica_requirement} GB."
            )

        # # Calculate model memory requirement
        announce("👉 Collecting model information....")
        if model_info is not None and model_config is not None:
            try:
                model_params = model_total_params(model_info)
                announce(f"ℹ️ {model} has a total of {model_params} parameters")

                model_mem_req = model_memory_req(model_info, model_config)
                announce(f"ℹ️ {model} requires {model_mem_req} GB of memory")

                # Estimate KV cache memory and max number of requests that can be served in worst case scenario
                if not skip_gpu_tests:
                    announce("👉 Estimating available KV cache....")
                    available_kv_cache = allocatable_kv_cache_memory(
                        model_info,
                        model_config,
                        gpu_memory,
                        gpu_memory_util,
                        tp=tp,
                        dp=dp,
                    )

                    if available_kv_cache < 0:
                        announce(
                            f"{msgtag} There is not enough GPU memory to stand up model. Exceeds by {abs(available_kv_cache)} GB."
                        )
                    else:
                        announce(
                            f"ℹ️ Allocatable memory for KV cache {available_kv_cache} GB"
                        )

                        kv_details = KVCacheDetail(
                            model_info, model_config, max_model_len, batch_size=1
                        )
                        announce(
                            f"ℹ️ KV cache memory for a request taking --max-model-len={max_model_len} requires {kv_details.per_request_kv_cache_gb} GB of memory"
                        )

                        total_concurrent_reqs = max_concurrent_requests(
                            model_info,
                            model_config,
                            max_model_len,
                            gpu_memory,
                            gpu_memory_util,
                            tp=tp,
                            dp=dp,
                        )
                        announce(
                            f"ℹ️ The vLLM server can process up to {total_concurrent_reqs} number of requests at the same time, assuming the worst case scenario that each request takes --max-model-len"
                        )

            except AttributeError as e:
                # Model might not have safetensors data on parameters
                announce(
                    f"{msgtag} Does not have enough information about model to estimate model memory or KV cache: {e}"
                )
        else:
            announce(f"{msgtag} Model info on model's architecture not available.")


def get_validation_param(ev: dict, type: str = COMMON) -> ValidationParam:
    """
    Returns validation param from type: one of prefill, decode, or None (default=common)
    """

    prefix = f"vllm_{COMMON}"
    if type == PREFILL or type == DECODE:
        prefix = f"vllm_modelservice_{type}"
    prefix = prefix.lower()

    models_list = ev["deploy_model_list"]
    models_list = [m.strip() for m in models_list.split(",")]
    replicas = ev[f"{prefix}_replicas"] or 0
    replicas = int(replicas)
    gpu_type = get_accelerator_type(ev)
    tp_size = int(ev[f"{prefix}_tensor_parallelism"])
    dp_size = int(ev[f"{prefix}_data_parallelism"])
    user_accelerator_nr = ev[f"{prefix}_accelerator_nr"]

    hf_token = ev["hf_token"]
    if hf_token == "":
        hf_token = None

    validation_param = ValidationParam(
        models=models_list,
        hf_token=hf_token,
        replicas=replicas,
        gpu_type=gpu_type,
        gpu_memory=convert_accelerator_memory(
            gpu_type, ev["vllm_common_accelerator_memory"]
        ),
        tp=tp_size,
        dp=dp_size,
        accelerator_nr=user_accelerator_nr,
        requested_accelerator_nr=get_accelerator_nr(
            user_accelerator_nr, tp_size, dp_size
        ),
        gpu_memory_util=float(ev[f"{prefix}_accelerator_mem_util"]),
        max_model_len=int(ev["vllm_common_max_model_len"]),
    )

    return validation_param


def validate_standalone_vllm_params(ev: dict, ignore_if_failed: bool):
    """
    Validates vllm standalone configuration. Returns True if validation is complete.
    """
    standalone_params = get_validation_param(ev)
    validate_vllm_params(standalone_params, ignore_if_failed)


def validate_modelservice_vllm_params(ev: dict, ignore_if_failed: bool):
    """
    Validates vllm modelservice configuration. Returns True if validation is complete.
    """
    prefill_params = get_validation_param(ev, type=PREFILL)
    decode_params = get_validation_param(ev, type=DECODE)

    announce(f"Validating prefill vLLM arguments for {prefill_params.models} ...")
    validate_vllm_params(prefill_params, ignore_if_failed, type=PREFILL)

    announce(f"Validating decode vLLM arguments for {decode_params.models} ...")
    validate_vllm_params(decode_params, ignore_if_failed, type=DECODE)


def capacity_planner_sanity_check(ev: dict):
    """
    Conducts a sanity check using the capacity planner library on standalone and modelservice deployments
    """

    # Capacity planning
    ignore_failed_validation = ev["ignore_failed_validation"]
    msg = "Validating vLLM configuration against Capacity Planner... "
    if ignore_failed_validation:
        msg += "deployment will continue even if validation failed."
    else:
        msg += "deployment will halt if validation failed."
    announce(msg)

    if is_standalone_deployment(ev):
        announce("Deployment method is standalone")
        validate_standalone_vllm_params(ev, ignore_failed_validation)
    else:
        announce(
            "Deployment method is modelservice, checking for prefill and decode deployments"
        )
        validate_modelservice_vllm_params(ev, ignore_failed_validation)


def get_random_node_port(min_port: int, max_port: int, api=None) -> int:
    """
    Return a random available NodePort in the given range.
    """
    if api is None:
        # Use pykube to connect to Kubernetes
        control_work_dir = os.environ.get(
            "LLMDBENCH_CONTROL_WORK_DIR", "/tmp/llm-d-benchmark"
        )
        api, client = kube_connect(f"{control_work_dir}/environment/context.ctx")

    existing_ports = set()
    services = pykube.Service.objects(api).all()
    for svc in services:
        ports = svc.obj.get("spec", {}).get("ports", [])
        for port in ports:
            node_port = port.get("nodePort")
            if node_port:
                existing_ports.add(node_port)
    while True:
        candidate = random.randint(min_port, max_port)
        if candidate not in existing_ports:
            return candidate


def find_accelerator_prefix(accelerators, affinity_string):
    """
    Find the first accelerator whose prefix exists in the given affinity string.
    """
    if not affinity_string:
        return None

    for accelerator in accelerators:
        if accelerator in affinity_string:
            return accelerator

    return None


def install_prometheus_adapters(
    prometheus_monitoring_ns: str,
    prometheus_base_url: str,
    prometheus_base_url_port: int,
    prometheus_ca_cert_path: str,
    dry_run: bool = False,
    verbose: bool = False,
):
    tmp_out_dir = tempfile.mkdtemp()
    prometheus_values_path = os.path.join(
        tmp_out_dir, "prometheus-adapter-values-ocp.yaml"
    )
    prometheus_rbac_values_path = os.path.join(
        tmp_out_dir, "prometheus-rbac-values-ocp.yaml"
    )

    prometheus_values_content = f"""
prometheus:
  url: {prometheus_base_url}
  port: {prometheus_base_url_port}

rules:
  external:
  - seriesQuery: 'inferno_desired_replicas{{variant_name!="",exported_namespace!=""}}'
    resources:
      overrides:
        exported_namespace: {{resource: "namespace"}}
        variant_name: {{resource: "deployment"}}
    name:
      matches: "^inferno_desired_replicas"
      as: "inferno_desired_replicas"
    metricsQuery: 'inferno_desired_replicas{{<<.LabelMatchers>>}}'

replicas: 2
logLevel: 4

tls:
  enable: false

extraVolumes:
  - name: prometheus-ca
    configMap:
      name: prometheus-ca

extraVolumeMounts:
  - name: prometheus-ca
    mountPath: /etc/prometheus-ca
    readOnly: true

extraArguments:
  - --prometheus-ca-file=/etc/prometheus-ca/ca.crt
  - --prometheus-token-file=/var/run/secrets/kubernetes.io/serviceaccount/token

podSecurityContext:
  fsGroup: null

securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: null
  seccompProfile:
    type: RuntimeDefault
""".lstrip()

    with open(prometheus_values_path, "w") as f:
        f.write(prometheus_values_content)

    prometheus_rbac_values_content = f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: allow-thanos-querier-api-access
rules:
- nonResourceURLs: [/api/v1/query, /api/v1/query_range, /api/v1/labels, /api/v1/label/*/values, /api/v1/series, /api/v1/metadata, /api/v1/rules, /api/v1/alerts]
  verbs: [get]
- apiGroups: [monitoring.coreos.com]
  resourceNames: [k8s]
  resources: [prometheuses/api]
  verbs: [get, create, update]
- apiGroups: [""]
  resources: [namespaces]
  verbs: [get]
""".lstrip()
    with open(prometheus_rbac_values_path, "w") as f:
        f.write(prometheus_rbac_values_content)

    cmd = (
        f"{os.getenv('LLMDBENCH_CONTROL_KCMD')} create configmap prometheus-ca "
        f"--from-file=ca.crt={prometheus_ca_cert_path} "
        f"-n {prometheus_monitoring_ns} "
        f"--dry-run=client -o yaml | "
        f"{os.getenv('LLMDBENCH_CONTROL_KCMD')} apply -f -"
    )
    llmdbench_execute_cmd(cmd, dry_run=dry_run, verbose=verbose)
    llmdbench_execute_cmd(
        f'{os.getenv("LLMDBENCH_CONTROL_HCMD")} repo add prometheus-community https://prometheus-community.github.io/helm-charts',
        dry_run=dry_run,
        verbose=verbose,
    )
    llmdbench_execute_cmd(
        f'{os.getenv("LLMDBENCH_CONTROL_HCMD")} repo update',
        dry_run=dry_run,
        verbose=verbose,
    )
    llmdbench_execute_cmd(
        f'{os.getenv("LLMDBENCH_CONTROL_HCMD")} upgrade -i prometheus-adapter prometheus-community/prometheus-adapter '
        f"-n {prometheus_monitoring_ns} -f {prometheus_values_path}",
        dry_run=dry_run,
        verbose=verbose,
    )
    llmdbench_execute_cmd(
        f'{os.getenv("LLMDBENCH_CONTROL_HCMD")} apply -f {prometheus_rbac_values_path}',
        dry_run=dry_run,
        verbose=verbose,
    )


def install_wva(wva_config, wva_namespace, dry_run=False, verbose=False):
    tmp_out_dir = tempfile.mkdtemp()
    wva_values_file = os.path.join(tmp_out_dir, "wva_config.yaml")
    namespace_manifest_file = os.path.join(tmp_out_dir, "wva_namespace.yaml")

    with open(wva_values_file, "w") as f:
        yaml.dump(wva_config, f, sort_keys=False)

    namespace_manifest = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": wva_namespace,
            "labels": {
                "openshift.io/user-monitoring": "true",
            },
        },
    }

    with open(namespace_manifest_file, "w") as f:
        yaml.dump(namespace_manifest, f, sort_keys=False)

    llmdbench_execute_cmd(
        f"{os.getenv('LLMDBENCH_CONTROL_KCMD')} apply -f {namespace_manifest_file}",
        dry_run=dry_run,
        verbose=verbose,
    )

    llmdbench_execute_cmd(
        f"{os.getenv('LLMDBENCH_CONTROL_HCMD')} upgrade -i workload-variant-autoscaler "
        f"{os.getenv('LLMDBENCH_WVA_HELM_REPOSITORY_URL')} "
        f"--version {os.getenv('LLMDBENCH_WVA_CHART_VERSION')} "
        f"-n {wva_namespace} "
        f"-f {wva_values_file}",
        dry_run=dry_run,
        verbose=verbose,
    )


def install_wva_components(ev: dict):
    # Use pykube to connect to Kubernetes
    control_work_dir = os.environ.get(
        "LLMDBENCH_CONTROL_WORK_DIR", "/tmp/llm-d-benchmark"
    )
    api, client = kube_connect(f"{control_work_dir}/environment/context.ctx")
    secret = (
        pykube.Secret.objects(api)
        .filter(namespace="openshift-monitoring")
        .get_by_name("thanos-querier-tls")
    )
    prom_ca_cert_path = Path(tempfile.mkdtemp()) / "prometheus-ca.crt"
    prom_ca_cert_path.write_bytes(base64.b64decode(secret.obj["data"]["tls.crt"]))
    formatted_cert = prom_ca_cert_path.read_text()
    if not formatted_cert.endswith("\n"):
        formatted_cert += "\n"
    formatted_cert = LiteralStr(formatted_cert)

    wva_config = {
        "wva": {
            "enabled": ev["wva_controller_enabled"],
            "image": {
                "repository": f"{ev['wva_image_repository']}",
                "tag": f"{ev['wva_image_tag']}",
            },
            "metrics": {
                "enabled": ev["wva_metrics_enabled"],
                "port": int(ev["wva_metrics_port"]),
                "secure": ev["wva_metrics_secure"],
            },
            "prometheus": {
                "baseURL": f"{ev['wva_prom_base_url']}:{ev['wva_prom_base_url_port']}",
                "caCert": formatted_cert,
                "monitoringNamespace": f"{ev['openshift_user_workload_monitoring_ns']}",
                "serviceAccountName": "prometheus-k8s",
                "tls": {
                    "insecureSkipVerify": "true",
                    "caCertPath": "/etc/ssl/certs/prometheus-ca.crt",
                },
            },
            "reconcileInterval": "60s",
            "scaleToZero": "false",
        },
        "llmd": {
            "namespace": f"{ev['vllm_common_namespace']}",
            "modelName": f"{ev['deploy_current_model_id_label']}",
            "modelID": f"{ev['deploy_current_model']}",
        },
        "va": {
            "enabled": ev["wva_variant_autoscaling_enabled"],
            "accelerator": f"{find_accelerator_prefix(['G2', 'A100', 'H100', 'L40S', 'MI300X'], ev['vllm_common_affinity'])}",
            "sloTpot": int(ev["wva_variant_autoscaling_slo_tpot"]),
            "sloTtft": int(ev["wva_variant_autoscaling_slo_ttft"]),
        },
        "hpa": {
            "enabled": ev["wva_hpa_enabled"],
            "maxReplicas": int(ev["wva_hpa_max_replicas"]),
            "targetAverageValue": int(f"{ev['wva_hpa_target_avg_value']}"),
        },
        "vllmService": {
            "enabled": ev["wva_vllm_service_enabled"],
            "nodePort": int(
                get_random_node_port(
                    int(ev["wva_vllm_service_node_port_min"]),
                    int(ev["wva_vllm_service_node_port_max"]),
                )
            ),
            "interval": f"{ev['wva_vllm_service_interval']}",
            "scheme": f"{ev['wva_vllm_service_scheme']}",
        },
    }

    #
    # NOTE: Due to inconsistent installation - we will need to use the SAME
    #       namespace as the model - seperating these will OFTEN
    #       result in VA never getting populated
    #
    install_wva(
        wva_config,
        ev["vllm_common_namespace"],
        dry_run=ev["control_dry_run"],
        verbose=ev["control_verbose"],
    )

    install_prometheus_adapters(
        ev["openshift_user_workload_monitoring_ns"],
        ev["wva_prom_base_url"],
        ev["wva_prom_base_url_port"],
        str(prom_ca_cert_path),
        dry_run=ev["control_dry_run"],
        verbose=ev["control_verbose"],
    )


