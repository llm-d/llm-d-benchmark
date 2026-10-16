#!/usr/bin/env bash
# Download HuggingFace models to a hostPath directory on ONE node (the
# "seed" node), then fan them out to every other node with DaemonSets that
# rsync from a seed pod. Only one node ever talks to HuggingFace.
#
# Only nodes where the hostPath directory is an actual mount point (e.g. a
# dedicated NVMe mounted at /var/model-cache) are used: a probe DaemonSet
# checks the host's /proc/1/mountinfo first, and both the seed and the sync
# DaemonSets are pinned to the nodes that pass.
#
# The models are handled in two loops:
#   1. Download: one pod per model on the seed node, run one after the other;
#      each exits once its model is on the seed node's hostPath.
#   2. Sync (skipped with --download-only): a single seed pod serves the
#      seed node's models over rsync, and one DaemonSet per model, run one
#      after the other, copies that model to every other mounted node.
#
# Layout on every node: <host-path>/models/<HF_MODEL_ID>/ plus a
# .download-complete marker (same layout the standup hostPath download uses).
#
# With --pv, each download pod first downloads into a PVC bound to that PV
# (same models/<HF_MODEL_ID>/ layout and marker), then copies it to the seed
# node's hostPath. The PVC is kept after the run, so the PV acts as a
# persistent cache: a later run that finds a model on it skips the HF download.
#
# Usage:
#   util/distribute_model_hostpath.sh -m <hf-model-id>[,<hf-model-id>...] [options]
#   util/distribute_model_hostpath.sh -m <models-file> [options]
#   util/distribute_model_hostpath.sh -f <models-file> [options]
#
# Options:
#   -m, --model IDS|FILE      HuggingFace model id(s), comma-separated (repeatable),
#                             e.g. facebook/opt-125m,Qwen/Qwen3-0.6B. If the value is an
#                             existing file, it is read as a models file (see -f).
#   -f, --models-file FILE    File with HuggingFace model ids, one per line or
#                             comma-separated; blank lines and '#' comments are ignored.
#                             At least one model (from -m and/or -f) is required.
#   -n, --namespace NS        Namespace (default: current kubectl context namespace)
#   -s, --seed-node NODE      Node that downloads from HF (default: first node with the mount)
#   -p, --host-path DIR       hostPath mount point on the nodes (default: /var/model-cache)
#   -l, --node-selector K=V   Only consider nodes with this label (repeatable)
#   -v, --pv NAME             Download to a PVC bound to this (Filesystem) PV first, then
#                             copy to the hostPath. The PVC (model-seed-<NAME>) is created
#                             if missing and never deleted by this script. If the PV has
#                             node affinity and --seed-node is not set, the scheduler picks
#                             the seed among the mounted nodes the PV can attach to.
#                             If the PV is ReadWriteMany and its CSI volumeHandle contains
#                             a /mnt path, that path is printed and the script
#                             exits without downloading anything.
#   -t, --hf-secret NAME      Secret holding key HF_TOKEN (default: llm-d-hf-token).
#                             Created from $HF_TOKEN if it does not exist.
#   -a, --service-account SA  Service account for the pods (default: default)
#   -i, --image IMAGE         Image with hf, rsync and chcon (default: ghcr.io/llm-d/llm-d-benchmark:nightly)
#   -c, --max-connections N   Concurrent rsync clients the seed serves (default: 4)
#   -T, --timeout DURATION    Wait timeout for each model's download and sync (default: 3600s)
#   -o, --download-only       Only run the download loop: every model is downloaded on the
#                             seed node (to the PV first, with --pv) and the script checks
#                             that each download pod completed successfully. No rsync
#                             daemon, no sync DaemonSets.
#   -k, --keep                Keep the download pods, seed pod and sync DaemonSets after the run
#   -d, --delete              Only delete the download pods, seed pod and DaemonSets, then exit
#   -D, --dry-run             Run the (read-only) mount probe, then print the download pod,
#                             seed pod and sync DaemonSet manifests and validate them with
#                             'kubectl apply --dry-run=server' instead of applying them
#   -h, --help                Show this help

set -euo pipefail

MODEL_ARGS=()
MODELS_FILE=""
NAMESPACE=$(kubectl config view --minify -o 'jsonpath={..namespace}' 2>/dev/null || true)
NAMESPACE=${NAMESPACE:-default}
SEED_NODE=""
HOST_PATH=/var/model-cache
NODE_SELECTORS=()
PV_NAME=""
HF_SECRET=llm-d-hf-token #pragma: allowlist secret
SERVICE_ACCOUNT=default
IMAGE=ghcr.io/llm-d/llm-d-benchmark:nightly
MAX_CONNECTIONS=4
RSYNC_PORT=8873
TIMEOUT=3600s
KEEP=0
DOWNLOAD_ONLY=0
DELETE_ONLY=0
DRY_RUN=0

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--model) MODEL_ARGS+=("$2"); shift 2 ;;
    -f|--models-file) MODELS_FILE=$2; shift 2 ;;
    -n|--namespace) NAMESPACE=$2; shift 2 ;;
    -s|--seed-node) SEED_NODE=$2; shift 2 ;;
    -p|--host-path) HOST_PATH=$2; shift 2 ;;
    -l|--node-selector) NODE_SELECTORS+=("$2"); shift 2 ;;
    -v|--pv) PV_NAME=$2; shift 2 ;;
    -t|--hf-secret) HF_SECRET=$2; shift 2 ;;
    -a|--service-account) SERVICE_ACCOUNT=$2; shift 2 ;;
    -i|--image) IMAGE=$2; shift 2 ;;
    -c|--max-connections) MAX_CONNECTIONS=$2; shift 2 ;;
    -T|--timeout) TIMEOUT=$2; shift 2 ;;
    -o|--download-only|--downloadonly) DOWNLOAD_ONLY=1; shift ;;
    -k|--keep) KEEP=1; shift ;;
    -d|--delete) DELETE_ONLY=1; shift ;;
    -D|--dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

# Prefix every line the script (and the commands it runs) prints with a
# timestamp. fd 3 keeps the raw stdout for the --dry-run manifests, so they
# stay valid YAML. On exit, close the pipes and wait for both filters, so
# no line is printed after the script returns.
timestamp_lines() {
  local line
  while IFS= read -r line || [[ -n "${line}" ]]; do
    printf '[%(%Y-%m-%d %H:%M:%S)T] %s\n' -1 "${line}"
  done
}
exec 3>&1 4>&2
exec 1> >(timestamp_lines)
TS_OUT_PID=$!
exec 2> >(timestamp_lines >&4)
TS_ERR_PID=$!
flush_timestamps() {
  exec 1>&3 2>&4
  wait "${TS_OUT_PID}" "${TS_ERR_PID}" 2>/dev/null || true
}
trap flush_timestamps EXIT

# Print every option (given or defaulted) up front. On stderr, because
# stdout carries the --dry-run manifests.
yes_no() { if [[ $1 -eq 1 ]]; then echo yes; else echo no; fi; }
{
  echo "==> Options:"
  echo "    --model            $(IFS=,; echo "${MODEL_ARGS[*]:-<not set>}")"
  echo "    --models-file      ${MODELS_FILE:-<not set>}"
  echo "    --namespace        ${NAMESPACE}"
  echo "    --seed-node        ${SEED_NODE:-<auto>}"
  echo "    --host-path        ${HOST_PATH}"
  echo "    --node-selector    ${NODE_SELECTORS[*]:-<none>}"
  echo "    --pv               ${PV_NAME:-<none>}"
  echo "    --hf-secret        ${HF_SECRET}"
  echo "    --service-account  ${SERVICE_ACCOUNT}"
  echo "    --image            ${IMAGE}"
  echo "    --max-connections  ${MAX_CONNECTIONS}"
  echo "    --timeout          ${TIMEOUT}"
  echo "    --download-only    $(yes_no "${DOWNLOAD_ONLY}")"
  echo "    --keep             $(yes_no "${KEEP}")"
  echo "    --delete           $(yes_no "${DELETE_ONLY}")"
  echo "    --dry-run          $(yes_no "${DRY_RUN}")"
} >&2

KCMD="kubectl -n ${NAMESPACE}"
SEED_POD=model-seed
DOWNLOAD_POD_PREFIX=model-download
DS_PREFIX=model-sync-ds
PROBE_DS_NAME=model-mount-probe
LABEL_SELECTOR="app=llm-d-benchmark-harness,component=model-distribute"
PVC_NAME=${PV_NAME:+model-seed-${PV_NAME}}

cleanup() {
  echo "==> Deleting download pods, seed pod ${SEED_POD} and DaemonSets (${LABEL_SELECTOR}) in ${NAMESPACE}"
  ${KCMD} delete daemonset -l "${LABEL_SELECTOR}" --ignore-not-found --wait=true
  ${KCMD} delete pod -l "${LABEL_SELECTOR}" --ignore-not-found --wait=true
  # Deleting a DaemonSet does not wait for its pods; wait so a rerun never
  # sees (or verifies through) a previous run's terminating pods.
  ${KCMD} wait --for=delete pod -l component=model-distribute --timeout=120s 2>/dev/null || true
}

# Apply the manifest on stdin. In --dry-run, print it and have the API
# server validate it (schema, admission/SCC) without persisting anything.
apply_manifest() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    local manifest
    manifest=$(cat)
    printf -- '---\n%s\n' "${manifest}" >&3
    printf '%s\n' "${manifest}" | ${KCMD} apply --dry-run=server -f - >&2
  else
    ${KCMD} apply -f -
  fi
}

# Print a required node affinity for the given nodes, with every line
# prefixed by INDENT. A metadata.name matchField takes a single value, so
# emit one (ORed) term per node.
node_affinity_yaml() {
  local indent=$1 node
  shift
  printf '%saffinity:\n%s  nodeAffinity:\n%s    requiredDuringSchedulingIgnoredDuringExecution:\n%s      nodeSelectorTerms:' \
    "${indent}" "${indent}" "${indent}" "${indent}"
  for node in "$@"; do
    printf '\n%s      - matchFields:\n%s        - key: metadata.name\n%s          operator: In\n%s          values: ["%s"]' \
      "${indent}" "${indent}" "${indent}" "${indent}" "${node}"
  done
}

if [[ ${DELETE_ONLY} -eq 1 ]]; then
  cleanup
  exit 0
fi

# --- Models: from -m (comma-separated) and -f (file) ----------------------
MODELS=()
add_models() {
  local item items
  IFS=',' read -ra items <<< "$1"
  for item in "${items[@]}"; do
    item=${item//[[:space:]]/}
    [[ -z "${item}" ]] && continue
    [[ " ${MODELS[*]} " == *" ${item} "* ]] || MODELS+=("${item}")
  done
}
# One model per line; blank lines and '#' comments are ignored.
add_models_from_file() {
  local line
  if [[ ! -r "$1" ]]; then
    echo "ERROR: models file $1 not found or not readable" >&2
    exit 1
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    add_models "${line%%#*}"
  done < "$1"
}
# A -m value that names an existing file is read as a models file.
for arg in "${MODEL_ARGS[@]}"; do
  if [[ -f "${arg}" ]]; then
    echo "==> Reading models from file ${arg}"
    add_models_from_file "${arg}"
  else
    add_models "${arg}"
  fi
done
if [[ -n "${MODELS_FILE}" ]]; then
  add_models_from_file "${MODELS_FILE}"
fi
if [[ ${#MODELS[@]} -eq 0 ]]; then
  echo "ERROR: at least one model is required (--model and/or --models-file)" >&2
  usage 1
fi
echo "==> Models (${#MODELS[@]}):"
for i in "${!MODELS[@]}"; do
  echo "    $((i + 1)). ${MODELS[${i}]}"
done

# --- PV: validate before touching the nodes --------------------------------
if [[ -n "${PV_NAME}" ]]; then
  PV_INFO=$(kubectl get pv "${PV_NAME}" -o 'jsonpath={.status.phase}|{.spec.claimRef.namespace}|{.spec.claimRef.name}|{.spec.volumeMode}|{.spec.storageClassName}|{.spec.capacity.storage}|{.spec.accessModes}|{.spec.csi.volumeHandle}' 2>/dev/null) || {
    echo "ERROR: PV ${PV_NAME} not found" >&2
    exit 1
  }
  IFS='|' read -r PV_PHASE PV_CLAIM_NS PV_CLAIM_NAME PV_VOLUME_MODE PV_STORAGE_CLASS PV_CAPACITY PV_ACCESS_MODES PV_VOLUME_HANDLE <<< "${PV_INFO}"
  # A ReadWriteMany PV whose CSI volumeHandle names a /mnt path is a shared
  # filesystem already present on the nodes: report that path and stop.
  PV_MNT_PATH=$(grep -o '/mnt[^;,#|[:space:]]*' <<< "${PV_VOLUME_HANDLE}" | head -n 1 || true)
  if [[ "${PV_ACCESS_MODES}" == *'"ReadWriteMany"'* && -n "${PV_MNT_PATH}" ]]; then
    echo "==> PV ${PV_NAME} is \"ReadWriteMany\" with path \"${PV_MNT_PATH}\"" >&2
  fi
  if [[ "${PV_VOLUME_MODE:-Filesystem}" != "Filesystem" ]]; then
    echo "ERROR: PV ${PV_NAME} has volumeMode ${PV_VOLUME_MODE}; only Filesystem is supported" >&2
    exit 1
  fi
  if [[ -n "${PV_CLAIM_NAME}" && "${PV_CLAIM_NS}/${PV_CLAIM_NAME}" != "${NAMESPACE}/${PVC_NAME}" ]]; then
    echo "ERROR: PV ${PV_NAME} (${PV_PHASE}) is claimed by ${PV_CLAIM_NS}/${PV_CLAIM_NAME}, not ${NAMESPACE}/${PVC_NAME}" >&2
    if [[ "${PV_PHASE}" == "Released" ]]; then
      echo "       If that claim is gone and the data may be reused, free the PV with:" >&2
      echo "       kubectl patch pv ${PV_NAME} --type json -p '[{\"op\":\"remove\",\"path\":\"/spec/claimRef\"}]'" >&2
    fi
    exit 1
  fi
  if [[ "${PV_PHASE}" == "Released" || "${PV_PHASE}" == "Failed" ]]; then
    echo "ERROR: PV ${PV_NAME} is ${PV_PHASE}; it cannot be bound until its claimRef is cleared" >&2
    exit 1
  fi
fi

# --- HF token secret -------------------------------------------------------
if ! ${KCMD} get secret "${HF_SECRET}" >/dev/null 2>&1; then
  if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "ERROR: secret ${HF_SECRET} not found in ${NAMESPACE} and HF_TOKEN is not set" >&2
    exit 1
  fi
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "==> [dry-run] Would create secret ${HF_SECRET} from \$HF_TOKEN"
  else
    echo "==> Creating secret ${HF_SECRET} from \$HF_TOKEN"
    ${KCMD} create secret generic "${HF_SECRET}" --from-literal=HF_TOKEN="${HF_TOKEN}"
  fi
fi

# --- Leftovers: pods and DaemonSets kept (--keep) by an earlier run -------
# Their names are reused below, and a pod's spec cannot be changed in place.
if [[ ${DRY_RUN} -eq 0 && -n "$(${KCMD} get pods,daemonsets -l "${LABEL_SELECTOR}" -o name 2>/dev/null)" ]]; then
  cleanup
fi

# --- Probe: which nodes have HOST_PATH as a real mount point --------------
# A hostPath with DirectoryOrCreate would silently land on the root disk of
# nodes without the dedicated mount, so find the mounted nodes first. The
# probe mounts nothing; it only reads the host's mount table (hostPID).
NODE_SELECTOR_YAML=""
if [[ ${#NODE_SELECTORS[@]} -gt 0 ]]; then
  NODE_SELECTOR_YAML="      nodeSelector:"
  for kv in "${NODE_SELECTORS[@]}"; do
    NODE_SELECTOR_YAML+=$'\n'"        \"${kv%%=*}\": \"${kv#*=}\""
  done
fi

echo "==> Probing nodes for a mount at ${HOST_PATH}"
cat <<EOF | ${KCMD} apply -f -
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${PROBE_DS_NAME}
  labels:
    app: llm-d-benchmark-harness
    component: model-distribute
    role: probe
spec:
  selector:
    matchLabels:
      app: llm-d-benchmark-harness
      component: model-distribute
      role: probe
  template:
    metadata:
      labels:
        app: llm-d-benchmark-harness
        component: model-distribute
        role: probe
    spec:
${NODE_SELECTOR_YAML}
      hostPID: true
      serviceAccountName: ${SERVICE_ACCOUNT}
      terminationGracePeriodSeconds: 0
      containers:
      - name: probe
        image: ${IMAGE}
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
        command: ["/bin/sh", "-c"]
        args:
        - |
          if awk -v p="${HOST_PATH}" '\$5 == p { found = 1 } END { exit !found }' /proc/1/mountinfo; then
            echo MOUNTED
          else
            echo NOT_MOUNTED
          fi
          exec sleep infinity
EOF

if ! ${KCMD} rollout status "daemonset/${PROBE_DS_NAME}" --timeout=300s; then
  echo "WARNING: not every probe pod started; nodes without a running probe are skipped" >&2
fi

MOUNTED_NODES=()
UNMOUNTED_NODES=()
while read -r pod node; do
  [[ -z "${pod}" ]] && continue
  if [[ "$(${KCMD} logs "${pod}" 2>/dev/null | head -1)" == "MOUNTED" ]]; then
    MOUNTED_NODES+=("${node}")
  else
    UNMOUNTED_NODES+=("${node}")
  fi
done < <(${KCMD} get pods -l role=probe,component=model-distribute \
           --field-selector=status.phase=Running \
           -o 'jsonpath={range .items[*]}{.metadata.name} {.spec.nodeName}{"\n"}{end}' | sort -k2)
${KCMD} delete daemonset "${PROBE_DS_NAME}" --ignore-not-found --wait=true

if [[ ${#UNMOUNTED_NODES[@]} -gt 0 ]]; then
  echo "==> Skipping nodes without a mount at ${HOST_PATH}: ${UNMOUNTED_NODES[*]}"
fi
if [[ ${#MOUNTED_NODES[@]} -eq 0 ]]; then
  echo "ERROR: no schedulable node has a mount at ${HOST_PATH}" >&2
  exit 1
fi
echo "==> Nodes with ${HOST_PATH} mounted (${#MOUNTED_NODES[@]}): ${MOUNTED_NODES[*]}"

# --- Seed node -------------------------------------------------------------
if [[ -n "${SEED_NODE}" && " ${MOUNTED_NODES[*]} " != *" ${SEED_NODE} "* ]]; then
  echo "ERROR: seed node ${SEED_NODE} does not have a mount at ${HOST_PATH}" >&2
  exit 1
fi

# Without a PV, pin the download pods with nodeName. With a PV, let the
# scheduler place the first one among the candidates so the PV's own node
# affinity (e.g. a local PV) is honoured; nodeName would bypass that check.
# Every later pod then goes to the node the first one landed on.
if [[ -z "${PV_NAME}" ]]; then
  SEED_NODE=${SEED_NODE:-${MOUNTED_NODES[0]}}
fi

echo "==> Namespace:  ${NAMESPACE}"
echo "==> Seed node:  ${SEED_NODE:-<scheduler picks a mounted node the PV can attach to>}"
echo "==> Host path:  ${HOST_PATH}/models/<model>"
if [[ -n "${PV_NAME}" ]]; then
  echo "==> PV:         ${PV_NAME} (${PV_CAPACITY}) via PVC ${PVC_NAME}"
fi

# Serving pods mount the node directory directly, so on SELinux nodes the
# files need a label non-spc_t containers can read. Non-SELinux nodes reject
# chcon; that is not an error there.
RELABEL='chcon -R -t container_file_t /var/model-cache || echo "chcon failed (SELinux not enabled?); continuing."'

# --- Download step ---------------------------------------------------------
# Spliced into the download pod script below (8-space indent; the heredoc
# does not re-expand the ${...} inside it).
if [[ -z "${PV_NAME}" ]]; then
  SEED_DOWNLOAD='        echo "Downloading model \"${HF_MODEL_ID}\" to ${TARGET_DIR}..."
        mkdir -p "${TARGET_DIR}"
        hf download "${HF_MODEL_ID}" --local-dir "${TARGET_DIR}"
        echo "Model \"${HF_MODEL_ID}\" downloaded."'
  SEED_PVC_MOUNT_YAML=""
  SEED_PVC_VOLUME_YAML=""
else
  # Download to the PVC (skipped when an earlier run left the model there),
  # then copy to the hostPath. --inplace avoids a 2x temp copy of shards.
  SEED_DOWNLOAD='        PVC_DIR="/var/model-pvc/${MODEL_PATH}"
        if [ -f "${PVC_DIR}/.download-complete" ]; then
          echo "Model \"${HF_MODEL_ID}\" already present on PVC at ${PVC_DIR}, skipping HF download."
        else
          echo "Downloading model \"${HF_MODEL_ID}\" to PVC at ${PVC_DIR}..."
          mkdir -p "${PVC_DIR}"
          hf download "${HF_MODEL_ID}" --local-dir "${PVC_DIR}"
          touch "${PVC_DIR}/.download-complete"
          echo "Model \"${HF_MODEL_ID}\" downloaded to PVC at ${PVC_DIR}"
        fi
        echo "Copying model \"${HF_MODEL_ID}\" from PVC to ${TARGET_DIR}..."
        mkdir -p "${TARGET_DIR}"
        rsync -a --inplace --exclude=.download-complete "${PVC_DIR}/" "${TARGET_DIR}/"
        echo "Model \"${HF_MODEL_ID}\" copied from PVC to ${TARGET_DIR}"'
  SEED_PVC_MOUNT_YAML="
    - name: model-pvc-storage
      mountPath: /var/model-pvc"
  SEED_PVC_VOLUME_YAML="
  - name: model-pvc-storage
    persistentVolumeClaim:
      claimName: ${PVC_NAME}"

  # --- PVC pre-bound to the PV (reused across runs) ------------------------
  if ${KCMD} get pvc "${PVC_NAME}" >/dev/null 2>&1; then
    echo "==> Reusing PVC ${PVC_NAME}"
  else
    echo "==> Creating PVC ${PVC_NAME} bound to PV ${PV_NAME}"
    # An empty storageClassName must be explicit, or the default class
    # would be injected and the PVC would never match the PV.
    cat <<EOF | apply_manifest
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${PVC_NAME}
  labels:
    app: llm-d-benchmark-harness
    component: model-distribute
spec:
  volumeName: ${PV_NAME}
  storageClassName: "${PV_STORAGE_CLASS}"
  accessModes: ${PV_ACCESS_MODES}
  volumeMode: Filesystem
  resources:
    requests:
      storage: ${PV_CAPACITY}
EOF
  fi
  if [[ ${DRY_RUN} -eq 0 ]] && ! ${KCMD} wait --for=jsonpath='{.status.phase}'=Bound "pvc/${PVC_NAME}" --timeout=120s; then
    echo "ERROR: PVC ${PVC_NAME} did not bind to PV ${PV_NAME}:" >&2
    ${KCMD} describe pvc "${PVC_NAME}" | tail -n 15 >&2 || true
    exit 1
  fi
fi

TIMEOUT_S=${TIMEOUT%s}
case "${TIMEOUT_S}" in
  *h) TIMEOUT_S=$(( ${TIMEOUT_S%h} * 3600 )) ;;
  *m) TIMEOUT_S=$(( ${TIMEOUT_S%m} * 60 )) ;;
esac

# Per-model results for the final summary.
declare -A DOWNLOAD_RESULT SYNC_RESULT
DOWNLOADED=()
ANY_FAILED=0

# --- Loop 1: download every model on the seed node ------------------------
# Each download pod runs to completion: it reports what it has, then exits.
for i in "${!MODELS[@]}"; do
  MODEL=${MODELS[${i}]}
  MODEL_PATH="models/${MODEL}"
  idx=$((i + 1))
  pod="${DOWNLOAD_POD_PREFIX}-${idx}"
  echo
  echo "==> [download ${idx}/${#MODELS[@]}] ${MODEL} on ${SEED_NODE:-<scheduled>}"

  if [[ -z "${PV_NAME}" ]]; then
    placement="  nodeName: ${SEED_NODE}"
  elif [[ -n "${SEED_NODE}" ]]; then
    placement=$(node_affinity_yaml "  " "${SEED_NODE}")
  else
    placement=$(node_affinity_yaml "  " "${MOUNTED_NODES[@]}")
  fi

  cat <<EOF | apply_manifest
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
  labels:
    app: llm-d-benchmark-harness
    component: model-distribute
    role: download
    model-index: "${idx}"
spec:
${placement}
  restartPolicy: Never
  serviceAccountName: ${SERVICE_ACCOUNT}
  terminationGracePeriodSeconds: 5
  securityContext:
    seLinuxOptions:
      type: spc_t
  containers:
  - name: download
    image: ${IMAGE}
    imagePullPolicy: IfNotPresent
    # Explicitly privileged so OpenShift admits it under the privileged SCC
    # (otherwise a more restrictive SCC with a read-only rootfs may win).
    securityContext:
      privileged: true
    command: ["/bin/sh", "-c"]
    args:
    - |
      set -e
      TARGET_DIR="/var/model-cache/\${MODEL_PATH}"
      MARKER="\${TARGET_DIR}/.download-complete"
      if [ -f "\${MARKER}" ]; then
        echo "Model \"\${HF_MODEL_ID}\" already present at node's hostpath (\${TARGET_DIR}), skipping download."
      else
${SEED_DOWNLOAD}
        echo "Relabelling for non-spc_t readers..."
        ${RELABEL}
        touch "\${MARKER}"
        echo "Download complete."
      fi
      echo "MODEL_STATS \$(du -sh "\${TARGET_DIR}" | cut -f1) \$(find "\${TARGET_DIR}" -type f ! -name .download-complete ! -path "*/.cache/*" | wc -l)"
    env:
    - name: MODEL_PATH
      value: ${MODEL_PATH}
    - name: HF_MODEL_ID
      value: ${MODEL}
    - name: HF_HOME
      value: /tmp/huggingface
    - name: HOME
      value: /tmp
    - name: HF_TOKEN
      valueFrom:
        secretKeyRef:
          name: ${HF_SECRET}
          key: HF_TOKEN
    volumeMounts:
    - name: model-host-storage
      mountPath: /var/model-cache${SEED_PVC_MOUNT_YAML}
  volumes:
  - name: model-host-storage
    hostPath:
      path: ${HOST_PATH}
      type: Directory${SEED_PVC_VOLUME_YAML}
EOF

  if [[ ${DRY_RUN} -eq 1 ]]; then
    DOWNLOAD_RESULT[${MODEL}]="dry-run"
    DOWNLOADED+=("${MODEL}")
    continue
  fi

  if [[ -n "${PV_NAME}" && -z "${SEED_NODE}" ]]; then
    # Fail fast when no candidate node can attach the PV, instead of
    # sitting Pending for the whole download timeout.
    if ! ${KCMD} wait --for=condition=PodScheduled "pod/${pod}" --timeout=120s; then
      echo "ERROR: download pod could not be scheduled on a mounted node the PV ${PV_NAME} can attach to:" >&2
      ${KCMD} get events --field-selector "involvedObject.name=${pod}" --sort-by=.lastTimestamp | tail -n 5 >&2 || true
      exit 1
    fi
    SEED_NODE=$(${KCMD} get pod "${pod}" -o 'jsonpath={.spec.nodeName}')
    echo "==> Download pod scheduled on ${SEED_NODE}; every model is downloaded there"
  fi

  # Poll the phase rather than 'kubectl wait', so a Failed pod is reported
  # right away instead of after the whole timeout.
  echo "==> Waiting for ${pod} to download ${MODEL} and complete (timeout ${TIMEOUT})"
  deadline=$(( SECONDS + TIMEOUT_S ))
  phase=""
  while (( SECONDS < deadline )); do
    phase=$(${KCMD} get pod "${pod}" -o 'jsonpath={.status.phase}' 2>/dev/null || true)
    [[ "${phase}" == "Succeeded" || "${phase}" == "Failed" ]] && break
    sleep 10
  done
  # Show all of the pod's logs before the next model's pod starts. Progress
  # bars redraw with carriage returns, which would hide every update but the
  # last one, so put each on its own line.
  logs=$(${KCMD} logs --timestamps "${pod}" 2>&1 | tr '\r' '\n' | sed '/^[[:space:]]*$/d' || true)
  echo "==> Logs of ${pod} (${MODEL}):"
  sed 's/^/    /' <<< "${logs}"
  stats=$(sed -n 's/^\([^ ]* \)\{0,1\}MODEL_STATS //p' <<< "${logs}" | tail -n 1)
  if [[ "${phase}" != "Succeeded" || -z "${stats}" || "${stats##* }" -le 0 ]]; then
    echo "ERROR: ${pod} did not download ${MODEL} successfully (phase: ${phase:-unknown}); see logs above" >&2
    DOWNLOAD_RESULT[${MODEL}]="FAILED (pod ${phase:-unknown})"
    ANY_FAILED=1
  else
    echo "==> ${MODEL} downloaded on ${SEED_NODE}: ${stats%% *}, ${stats##* } files"
    DOWNLOAD_RESULT[${MODEL}]="downloaded (${stats%% *}, ${stats##* } files)"
    DOWNLOADED+=("${MODEL}")
  fi
  if [[ ${KEEP} -eq 0 ]]; then
    ${KCMD} delete pod "${pod}" --ignore-not-found --wait=true
  fi
done

# Print the per-model results and exit (0 only if every model succeeded).
finish() {
  local model
  echo
  echo "==> Summary: ${#MODELS[@]} models, seed node ${SEED_NODE:-<scheduled>}, at ${HOST_PATH}/models/"
  printf '    %-48s %-36s %s\n' MODEL DOWNLOAD SYNC
  for model in "${MODELS[@]}"; do
    printf '    %-48s %-36s %s\n' "${model}" "${DOWNLOAD_RESULT[${model}]:--}" "${SYNC_RESULT[${model}]:--}"
  done
  echo
  if [[ ${KEEP} -eq 1 && ${DRY_RUN} -eq 0 ]]; then
    echo "==> Keeping download pods, seed pod and sync DaemonSets (--keep). Remove with: $0 -n ${NAMESPACE} --delete"
  fi
  if [[ -n "${PV_NAME}" && ${DRY_RUN} -eq 0 ]]; then
    echo "==> Keeping PVC ${PVC_NAME} (models cached on PV ${PV_NAME}). Remove with: kubectl -n ${NAMESPACE} delete pvc ${PVC_NAME}"
  fi
  if [[ ${ANY_FAILED} -eq 1 ]]; then
    echo "==> ERROR: not every model was downloaded and synced (see summary above)" >&2
    exit 1
  fi
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "==> [dry-run] Nothing applied. $1"
  else
    echo "==> SUCCESS: $1"
  fi
  exit 0
}

if [[ ${DOWNLOAD_ONLY} -eq 1 ]]; then
  finish "${#DOWNLOADED[@]} models downloaded on seed node ${SEED_NODE:-<scheduled>} (--download-only, not synced to other nodes)"
fi
if [[ ${#DOWNLOADED[@]} -eq 0 ]]; then
  echo "ERROR: no model was downloaded; nothing to sync" >&2
  finish ""
fi

# --- Seed pod: serve the seed node's models over rsync daemon -------------
if [[ -n "${SEED_NODE}" ]]; then
  placement="  nodeName: ${SEED_NODE}"
else
  # Only in --dry-run with a PV, where no download pod was scheduled.
  placement=$(node_affinity_yaml "  " "${MOUNTED_NODES[@]}")
fi
echo
echo "==> Starting seed pod ${SEED_POD} on ${SEED_NODE:-<scheduled>} to serve ${HOST_PATH}/models over rsync"
cat <<EOF | apply_manifest
apiVersion: v1
kind: Pod
metadata:
  name: ${SEED_POD}
  labels:
    app: llm-d-benchmark-harness
    component: model-distribute
    role: seed
spec:
${placement}
  restartPolicy: Always
  serviceAccountName: ${SERVICE_ACCOUNT}
  terminationGracePeriodSeconds: 5
  securityContext:
    seLinuxOptions:
      type: spc_t
  containers:
  - name: seed
    image: ${IMAGE}
    imagePullPolicy: IfNotPresent
    securityContext:
      privileged: true
    command: ["/bin/sh", "-c"]
    args:
    - |
      cat > /tmp/rsyncd.conf <<CONF
      pid file = /tmp/rsyncd.pid
      lock file = /tmp/rsyncd.lock
      use chroot = no
      uid = 0
      gid = 0
      port = ${RSYNC_PORT}
      max connections = ${MAX_CONNECTIONS}
      [models]
        path = /var/model-cache/models
        read only = yes
        list = yes
      CONF
      echo "Serving /var/model-cache/models over rsync on port ${RSYNC_PORT}"
      exec rsync --daemon --no-detach --log-file=/dev/stdout --config=/tmp/rsyncd.conf
    ports:
    - name: rsync
      containerPort: ${RSYNC_PORT}
    readinessProbe:
      tcpSocket:
        port: ${RSYNC_PORT}
      periodSeconds: 10
    volumeMounts:
    - name: model-host-storage
      mountPath: /var/model-cache
  volumes:
  - name: model-host-storage
    hostPath:
      path: ${HOST_PATH}
      type: Directory
EOF

if [[ ${DRY_RUN} -eq 1 ]]; then
  # The seed pod does not exist, so there is no IP to sync from yet.
  SEED_IP="<seed-pod-ip>"
else
  if ! ${KCMD} wait --for=condition=Ready "pod/${SEED_POD}" --timeout=300s; then
    echo "ERROR: seed pod did not become Ready; logs:" >&2
    ${KCMD} logs --timestamps "${SEED_POD}" >&2 || true
    exit 1
  fi
  SEED_IP=$(${KCMD} get pod "${SEED_POD}" -o 'jsonpath={.status.podIP}')
  echo "==> Seed pod ready at ${SEED_IP}"
fi

# Sync DaemonSets run only on the nodes the probe found with the mount.
NODE_AFFINITY_YAML=$(node_affinity_yaml "      " "${MOUNTED_NODES[@]}")

# Worker nodes (narrowed by --node-selector) for the per-node summaries; fall
# back to all nodes on clusters that do not label workers.
worker_selector="node-role.kubernetes.io/worker"
if [[ ${#NODE_SELECTORS[@]} -gt 0 ]]; then
  worker_selector+=",$(IFS=,; echo "${NODE_SELECTORS[*]}")"
fi
mapfile -t WORKER_NODES < <(kubectl get nodes -l "${worker_selector}" -o name | sed 's|^node/||' | sort)
if [[ ${#WORKER_NODES[@]} -eq 0 ]]; then
  mapfile -t WORKER_NODES < <(kubectl get nodes -o name | sed 's|^node/||' | sort)
fi

# --- Loop 2: sync every downloaded model to every other mounted node ------
SKIPPED=0
for i in "${!MODELS[@]}"; do
  MODEL=${MODELS[${i}]}
  if [[ " ${DOWNLOADED[*]} " != *" ${MODEL} "* ]]; then
    SYNC_RESULT[${MODEL}]="not run (download failed)"
    continue
  fi
  MODEL_PATH="models/${MODEL}"
  idx=$((i + 1))
  ds="${DS_PREFIX}-${idx}"
  echo
  echo "==> [sync ${idx}/${#MODELS[@]}] ${MODEL} to ${#MOUNTED_NODES[@]} nodes"

  cat <<EOF | apply_manifest
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${ds}
  labels:
    app: llm-d-benchmark-harness
    component: model-distribute
    role: sync
    model-index: "${idx}"
spec:
  selector:
    matchLabels:
      app: llm-d-benchmark-harness
      component: model-distribute
      role: sync
      model-index: "${idx}"
  template:
    metadata:
      labels:
        app: llm-d-benchmark-harness
        component: model-distribute
        role: sync
        model-index: "${idx}"
    spec:
${NODE_AFFINITY_YAML}
      serviceAccountName: ${SERVICE_ACCOUNT}
      terminationGracePeriodSeconds: 5
      securityContext:
        seLinuxOptions:
          type: spc_t
      containers:
      - name: sync
        image: ${IMAGE}
        imagePullPolicy: IfNotPresent
        securityContext:
          privileged: true
        command: ["/bin/sh", "-c"]
        args:
        - |
          TARGET_DIR="/var/model-cache/\${MODEL_PATH}"
          MARKER="\${TARGET_DIR}/.download-complete"
          if [ -f "\${MARKER}" ]; then
            echo "Model already present at \${TARGET_DIR} on \${NODE_NAME}, skipping sync."
            exec sleep infinity
          fi
          mkdir -p "\${TARGET_DIR}"
          # The seed caps concurrent clients (max connections), so retry
          # until a slot frees up. --inplace writes straight into the final
          # files (no temp copy, so no 2x space for large shards) and lets a
          # retry continue from what an interrupted run already wrote. The
          # marker is only touched after rsync succeeds, so a half-written
          # model is never reported as ready.
          until rsync -a --inplace --exclude=.download-complete \
                "rsync://\${SEED_IP}:${RSYNC_PORT}/models/\${MODEL}/" "\${TARGET_DIR}/"; do
            echo "rsync from \${SEED_IP} failed or seed busy; retrying in 15s..."
            sleep 15
          done
          echo "Relabelling for non-spc_t readers..."
          ${RELABEL}
          touch "\${MARKER}"
          echo "Sync complete on \${NODE_NAME}."
          exec sleep infinity
        env:
        - name: MODEL
          value: ${MODEL}
        - name: MODEL_PATH
          value: ${MODEL_PATH}
        - name: SEED_IP
          value: "${SEED_IP}"
        - name: NODE_NAME
          valueFrom:
            fieldRef:
              fieldPath: spec.nodeName
        readinessProbe:
          exec:
            command: ["/bin/sh", "-c", "test -f \"/var/model-cache/\${MODEL_PATH}/.download-complete\""]
          periodSeconds: 10
        volumeMounts:
        - name: model-host-storage
          mountPath: /var/model-cache
      volumes:
      - name: model-host-storage
        hostPath:
          path: ${HOST_PATH}
          type: Directory
EOF

  if [[ ${DRY_RUN} -eq 1 ]]; then
    SYNC_RESULT[${MODEL}]="dry-run"
    continue
  fi

  echo "==> Waiting for DaemonSet ${ds} to sync ${MODEL} to ${#MOUNTED_NODES[@]} nodes (timeout ${TIMEOUT})"
  if ! ${KCMD} rollout status "daemonset/${ds}" --timeout="${TIMEOUT}"; then
    echo "ERROR: DaemonSet ${ds} did not finish; pods not ready:" >&2
    ${KCMD} get pods -l "role=sync,component=model-distribute,model-index=${idx}" -o wide | grep -v ' 1/1 ' >&2 || true
  fi

  # --- Verify: check the model on every node and report per worker node --
  # Rollout status only says the readiness probes passed; look at the files
  # on each node directly (marker, size, file count) through its sync pod.
  unset SYNC_POD_ON NODE_STATUS NODE_SIZE NODE_FILES
  declare -A SYNC_POD_ON NODE_STATUS NODE_SIZE NODE_FILES
  # Skip pods being deleted.
  while read -r pod node deleting; do
    [[ -n "${pod}" && -z "${deleting}" ]] && SYNC_POD_ON[${node}]=${pod}
  done < <(${KCMD} get pods -l "role=sync,component=model-distribute,model-index=${idx}" \
             -o 'jsonpath={range .items[*]}{.metadata.name} {.spec.nodeName} {.metadata.deletionTimestamp}{"\n"}{end}')

  VERIFIED=0
  FAILED=0
  for node in "${MOUNTED_NODES[@]}"; do
    NODE_SIZE[${node}]="-"
    NODE_FILES[${node}]="-"
    pod=${SYNC_POD_ON[${node}]:-}
    if [[ -z "${pod}" ]]; then
      NODE_STATUS[${node}]="FAILED (no sync pod)"
      FAILED=$((FAILED + 1))
      continue
    fi
    # Prints "<size> <file count>" when the marker is present, nothing otherwise.
    result=$(${KCMD} exec "${pod}" -- sh -c '
      d="/var/model-cache/'"${MODEL_PATH}"'"
      [ -f "$d/.download-complete" ] || exit 1
      echo "$(du -sh "$d" | cut -f1) $(find "$d" -type f ! -name .download-complete ! -path "*/.cache/*" | wc -l)"
    ' 2>/dev/null || true)
    if [[ -n "${result}" && "${result##* }" -gt 0 ]]; then
      NODE_STATUS[${node}]="downloaded"
      NODE_SIZE[${node}]=${result%% *}
      NODE_FILES[${node}]=${result##* }
      VERIFIED=$((VERIFIED + 1))
    else
      NODE_STATUS[${node}]="FAILED (model missing)"
      FAILED=$((FAILED + 1))
    fi
  done
  for node in "${UNMOUNTED_NODES[@]}"; do
    NODE_STATUS[${node}]="skipped (no mount at ${HOST_PATH})"
  done
  SKIPPED=0
  for node in "${WORKER_NODES[@]}"; do
    if [[ -z "${NODE_STATUS[${node}]:-}" ]]; then
      NODE_STATUS[${node}]="skipped (not schedulable: tainted, cordoned or NotReady)"
    fi
    [[ "${NODE_STATUS[${node}]}" == skipped* ]] && SKIPPED=$((SKIPPED + 1))
  done

  echo
  echo "==> Sync summary: ${MODEL} at ${HOST_PATH}/${MODEL_PATH}"
  printf '    %-28s %-8s %-6s %s\n' NODE SIZE FILES STATUS
  for node in $(printf '%s\n' "${!NODE_STATUS[@]}" | sort); do
    printf '    %-28s %-8s %-6s %s\n' "${node}" "${NODE_SIZE[${node}]:--}" "${NODE_FILES[${node}]:--}" "${NODE_STATUS[${node}]}"
  done

  if [[ ${FAILED} -gt 0 ]]; then
    echo "==> ERROR: ${MODEL} is missing on ${FAILED} of ${#MOUNTED_NODES[@]} nodes with ${HOST_PATH} mounted" >&2
    SYNC_RESULT[${MODEL}]="FAILED on ${FAILED} of ${#MOUNTED_NODES[@]} nodes"
    ANY_FAILED=1
  else
    SYNC_RESULT[${MODEL}]="synced to all ${VERIFIED} nodes"
  fi

  if [[ ${KEEP} -eq 0 ]]; then
    ${KCMD} delete daemonset "${ds}" --ignore-not-found --wait=true
    ${KCMD} wait --for=delete pod -l "role=sync,component=model-distribute,model-index=${idx}" --timeout=120s 2>/dev/null || true
  fi
done

if [[ ${KEEP} -eq 0 && ${DRY_RUN} -eq 0 ]]; then
  cleanup
fi

if [[ ${DRY_RUN} -eq 1 ]]; then
  finish "Would download ${#DOWNLOADED[@]} models on ${SEED_NODE:-<scheduled>} and sync them to ${#MOUNTED_NODES[@]} nodes: ${MOUNTED_NODES[*]}"
elif [[ ${SKIPPED} -eq 0 ]]; then
  finish "${#DOWNLOADED[@]} models are downloaded on all ${#MOUNTED_NODES[@]} worker nodes"
else
  finish "${#DOWNLOADED[@]} models are downloaded on all ${#MOUNTED_NODES[@]} worker nodes with ${HOST_PATH} mounted (${SKIPPED} of ${#WORKER_NODES[@]} worker nodes skipped, see sync summaries above)"
fi
