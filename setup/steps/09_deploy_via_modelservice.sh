#!/usr/bin/env bash
source ${LLMDBENCH_CONTROL_DIR}/env.sh

if [[ $LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_MODELSERVICE_ACTIVE -eq 1 ]]; then

  check_storage_class
  if [[ $? -ne 0 ]]
  then
    announce "❌ Failed to check storage class"
    exit 1
  fi

  check_affinity
  if [[ $? -ne 0 ]]
  then
    announce "❌ Failed to check affinity"
    exit 1
  fi

  extract_environment

  # deploy models
  model_number=0
  for model in ${LLMDBENCH_DEPLOY_MODEL_LIST//,/ }; do

    export LLMDBENCH_DEPLOY_CURRENT_MODEL=$(model_attribute $model model)
    export LLMDBENCH_DEPLOY_CURRENT_MODEL_ID=$(model_attribute $model modelid)
    export LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL=$(model_attribute $model modelid_label)
    export LLMDBENCH_DEPLOY_CURRENT_SERVICE_NAME="$(model_attribute $model modelid_label)-gaie-epp"

    mount_model_volume=false
    if [[ $LLMDBENCH_VLLM_MODELSERVICE_URI_PROTOCOL == "pvc" || ${LLMDBENCH_CONTROL_ENVIRONMENT_TYPE_STANDALONE_ACTIVE} -eq 1 ]]; then
      export LLMDBENCH_VLLM_MODELSERVICE_URI="pvc://${LLMDBENCH_VLLM_COMMON_PVC_NAME}/models/$(model_attribute $model model)"
      mount_model_volume=true
    else
      export LLMDBENCH_VLLM_MODELSERVICE_URI="hf://$(model_attribute $model model)"
      mount_model_volume=true
    fi

    if [[ -n $LLMDBENCH_VLLM_MODELSERVICE_MOUNT_MODEL_VOLUME_OVERRIDE ]]; then
      mount_model_volume=$LLMDBENCH_VLLM_MODELSERVICE_MOUNT_MODEL_VOLUME_OVERRIDE
    fi

    # Do not use "llmdbench_execute_cmd" for these commands. Those need to executed even on "dry-run"
    printf -v MODEL_NUM "%02d" "$model_number"
    mkdir -p ${LLMDBENCH_CONTROL_WORK_DIR}/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}

    echo -n "" > $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}/ms-rules.yaml
    if [[ "${LLMDBENCH_DEPLOY_MODEL_LIST}" != *","* ]]; then
      cat << EOF > $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}/ms-rules.yaml
- backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: ${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-gaie
        port: 8000
        weight: 1
      timeouts:
        backendRequest: 0s
        request: 0s
EOF
    fi

    cat << EOF >$LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}/ms-values.yaml
fullnameOverride: ${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}
multinode: ${LLMDBENCH_VLLM_MODELSERVICE_MULTINODE}
#############
modelArtifacts:
  uri: $LLMDBENCH_VLLM_MODELSERVICE_URI
  size: $LLMDBENCH_VLLM_COMMON_PVC_MODEL_CACHE_SIZE
  authSecretName: "llm-d-hf-token"
  name: $(model_attribute $model model)
#############
routing:
  servicePort: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
  parentRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway
  proxy:
    image: "$(get_image ${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_REGISTRY} ${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_REPO} ${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_NAME} ${LLMDBENCH_LLMD_ROUTINGSIDECAR_IMAGE_TAG} 0)"
    secure: false
    connector: ${LLMDBENCH_LLMD_ROUTINGSIDECAR_CONNECTOR}
    debugLevel: ${LLMDBENCH_LLMD_ROUTINGSIDECAR_DEBUG_LEVEL}
  inferenceModel:
    create: ${LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_MODEL}
  inferencePool:
    create: ${LLMDBENCH_VLLM_MODELSERVICE_INFERENCE_POOL}
    name: ${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-gaie
  httpRoute:
    create: ${LLMDBENCH_VLLM_MODELSERVICE_ROUTE}
    rules:
    - backendRefs:
      - group: inference.networking.x-k8s.io
        kind: InferencePool
        name: ${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-gaie
        port: 8000
        weight: 1
      timeouts:
        backendRequest: 0s
        request: 0s
      matches:
      - path:
          type: PathPrefix
          value: /${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID}/
      filters:
      - type: URLRewrite
        urlRewrite:
          path:
            type: ReplacePrefixMatch
            replacePrefixMatch: /
    $(cat $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}/ms-rules.yaml)
  epp:
    create: ${LLMDBENCH_VLLM_MODELSERVICE_EPP}
#############
decode:
  create: $(echo $LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS | $LLMDBENCH_CONTROL_SCMD -e 's/^0/false/' -e 's/[1-9].*/true/')
  replicas: ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS}
  acceleratorTypes:
      labelKey: $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
      labelValues:
        - $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
  parallelism:
    data: ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM}
    tensor: ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM}
  annotations:
      $(add_annotations LLMDBENCH_VLLM_COMMON_ANNOTATIONS)
  podAnnotations:
      $(add_annotations LLMDBENCH_VLLM_MODELSERVICE_DECODE_PODANNOTATIONS)
  $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_POD_CONFIG} 2 extraConfig)
  containers:
  - name: "vllm"
    mountModelVolume: $mount_model_volume
    image: "$(get_image ${LLMDBENCH_LLMD_IMAGE_REGISTRY} ${LLMDBENCH_LLMD_IMAGE_REPO} ${LLMDBENCH_LLMD_IMAGE_NAME} ${LLMDBENCH_LLMD_IMAGE_TAG} 0)"
    modelCommand: ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND}
    $(add_command $LLMDBENCH_VLLM_MODELSERVICE_DECODE_MODEL_COMMAND)
    args:
      $(add_command_line_options ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_ARGS})
    env:
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      $(add_additional_env_to_yaml $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML)
    resources:
      limits:
        memory: $LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EPHEMERAL_STORAGE_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"$(get_accelerator_nr $LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR $LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM $LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM)\"" | $LLMDBENCH_CONTROL_SCMD -e "s^: \"\"^^g")
        $(echo "$LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
      requests:
        memory: $LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_MODELSERVICE_DECODE_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EPHEMERAL_STORAGE_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"$(get_accelerator_nr $LLMDBENCH_VLLM_MODELSERVICE_DECODE_ACCELERATOR_NR $LLMDBENCH_VLLM_MODELSERVICE_DECODE_TENSOR_PARALLELISM $LLMDBENCH_VLLM_MODELSERVICE_DECODE_DATA_PARALLELISM)\"" | $LLMDBENCH_CONTROL_SCMD -e "s^: \"\"^^g")
        $(echo "$LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_DECODE_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT}
        failureThreshold: 60
        initialDelaySeconds: ${LLMDBENCH_VLLM_COMMON_INITIAL_DELAY_PROBE}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_INFERENCE_PORT}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: 8200
        failureThreshold: 3
        periodSeconds: 5
    $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_CONTAINER_CONFIG} 6)
    volumeMounts: $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUME_MOUNTS} 4)
  volumes: $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_DECODE_EXTRA_VOLUMES} 2)
#############
prefill:
  create: $(echo $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS | $LLMDBENCH_CONTROL_SCMD -e 's/^0/false/' -e 's/[1-9].*/true/')
  replicas: ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS}
  acceleratorTypes:
      labelKey: $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 1)
      labelValues:
        - $(echo $LLMDBENCH_VLLM_COMMON_AFFINITY | cut -d ':' -f 2)
  parallelism:
    data: ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM}
    tensor: ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM}
  annotations:
      $(add_annotations LLMDBENCH_VLLM_COMMON_ANNOTATIONS)
  podAnnotations:
      $(add_annotations LLMDBENCH_VLLM_MODELSERVICE_PREFILL_PODANNOTATIONS)
  $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_POD_CONFIG} 2 extraConfig)
  containers:
  - name: "vllm"
    mountModelVolume: $mount_model_volume
    image: "$(get_image ${LLMDBENCH_LLMD_IMAGE_REGISTRY} ${LLMDBENCH_LLMD_IMAGE_REPO} ${LLMDBENCH_LLMD_IMAGE_NAME} ${LLMDBENCH_LLMD_IMAGE_TAG} 0)"
    modelCommand: ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND}
    $(add_command $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_MODEL_COMMAND)
    args:
      $(add_command_line_options ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_ARGS})
    env:
      - name: VLLM_IS_PREFILL
        value: "1"
      - name: VLLM_NIXL_SIDE_CHANNEL_HOST
        valueFrom:
          fieldRef:
            fieldPath: status.podIP
      $(add_additional_env_to_yaml $LLMDBENCH_VLLM_COMMON_ENVVARS_TO_YAML)
    resources:
      limits:
        memory: $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EPHEMERAL_STORAGE_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"$(get_accelerator_nr $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)\"" | $LLMDBENCH_CONTROL_SCMD -e "s^: \"\"^^g")
        $(echo "$LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
      requests:
        memory: $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_MEM
        cpu: "$LLMDBENCH_VLLM_MODELSERVICE_PREFILL_CPU_NR"
        $(echo "$LLMDBENCH_VLLM_COMMON_EPHEMERAL_STORAGE_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EPHEMERAL_STORAGE_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
        $(echo "$LLMDBENCH_VLLM_COMMON_ACCELERATOR_RESOURCE: \"$(get_accelerator_nr $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_ACCELERATOR_NR $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_TENSOR_PARALLELISM $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_DATA_PARALLELISM)\"" | $LLMDBENCH_CONTROL_SCMD -e "s^: \"\"^^g")
        $(echo "$LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_RESOURCE: \"${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_NETWORK_NR}\"" | $LLMDBENCH_CONTROL_SCMD -e 's/^: \"\"//')
    extraConfig:
      startupProbe:
        httpGet:
          path: /health
          port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
        failureThreshold: 60
        initialDelaySeconds: ${LLMDBENCH_VLLM_COMMON_INITIAL_DELAY_PROBE}
        periodSeconds: 30
        timeoutSeconds: 5
      livenessProbe:
        tcpSocket:
          port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
        failureThreshold: 3
        periodSeconds: 5
      readinessProbe:
        httpGet:
          path: /health
          port: ${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT}
        failureThreshold: 3
        periodSeconds: 5
    $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_CONTAINER_CONFIG} 6)
    $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUME_MOUNTS} 4 volumeMounts)
  $(add_config ${LLMDBENCH_VLLM_MODELSERVICE_PREFILL_EXTRA_VOLUMES} 2 volumes)
EOF
    # cleanup temp file
    rm -f $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/${MODEL_NUM}/ms-rules.yaml

    announce "🚀 Installing helm chart \"ms-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}\" via helmfile..."
    llmdbench_execute_cmd "helmfile --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} --kubeconfig ${LLMDBENCH_CONTROL_WORK_DIR}/environment/context.ctx --selector name=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-ms apply -f $LLMDBENCH_CONTROL_WORK_DIR/setup/helm/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}/helmfile-${MODEL_NUM}.yaml --skip-diff-on-install --skip-schema-validation" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ ${LLMDBENCH_VLLM_COMMON_NAMESPACE}-${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL}-ms helm chart deployed successfully"

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS -gt 0 ]]; then
      announce "⏳ waiting for (decode) pods serving model ${model} to be created..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=$((LLMDBENCH_CONTROL_WAIT_TIMEOUT / 2))s --for=create pod -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=decode" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 2
      announce "✅ (decode) pods serving model ${model} created"
    fi

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS -gt 0 ]]; then
      announce "⏳ waiting for (prefill) pods serving model ${model} to be created..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=$((LLMDBENCH_CONTROL_WAIT_TIMEOUT / 2))s --for=create pod -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=prefill" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE} 1 2
      announce "✅ (prefill) pods serving model ${model} created"
    fi

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS -gt 0 ]]; then
      announce "⏳ Waiting for (decode) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod  -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=decode" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "🚀 (decode) pods serving model ${model} running"
    fi

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS -gt 0 ]]; then
      announce "⏳ Waiting for (prefill) pods serving model ${model} to be in \"Running\" state (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=jsonpath='{.status.phase}'=Running pod  -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=prefill" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "🚀 (prefill) pods serving model ${model} running"
    fi

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_DECODE_REPLICAS -gt 0 ]]; then
      announce "⏳ Waiting for (decode) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=decode" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "🚀 (decode) pods serving model ${model} ready"

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} logs --tail=-1 --prefix=true -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=decode > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/logs/llm-d-decode.log" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    fi

    if [[ $LLMDBENCH_VLLM_MODELSERVICE_PREFILL_REPLICAS -gt 0 ]]; then
      announce "⏳ Waiting for (prefill) pods serving ${model} to be Ready (timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s)..."
      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} wait --timeout=${LLMDBENCH_CONTROL_WAIT_TIMEOUT}s --for=condition=Ready=True pod -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=prefill" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
      announce "🚀 (prefill) pods serving model ${model} ready"

      llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} logs --tail=-1 --prefix=true -l llm-d.ai/model=${LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL},llm-d.ai/role=prefill > ${LLMDBENCH_CONTROL_WORK_DIR}/setup/logs/llm-d-prefill.log" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}

    fi

    announce "📜 Labelling gateway for model ${model} "
    llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} label gateway/infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway stood-up-by=$LLMDBENCH_CONTROL_USERNAME stood-up-from=llm-d-benchmark stood-up-via=$LLMDBENCH_DEPLOY_METHODS" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
    announce "✅ Service for pods service model ${model} created"


    if [[ $LLMDBENCH_VLLM_MODELSERVICE_ROUTE == "true" && $LLMDBENCH_CONTROL_DEPLOY_IS_OPENSHIFT -eq 1 ]]; then
      is_route=$(${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} get route -o name --ignore-not-found | grep -E "/${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway-route$" || true)
      if [[ -z $is_route ]]
      then
        announce "📜 Exposing pods serving model ${model} as service..."
        llmdbench_execute_cmd "${LLMDBENCH_CONTROL_KCMD} --namespace ${LLMDBENCH_VLLM_COMMON_NAMESPACE} expose service/infra-${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway --target-port=${LLMDBENCH_VLLM_COMMON_INFERENCE_PORT} --name=${LLMDBENCH_VLLM_MODELSERVICE_RELEASE}-inference-gateway-route" ${LLMDBENCH_CONTROL_DRY_RUN} ${LLMDBENCH_CONTROL_VERBOSE}
        announce "✅ Service for pods service model ${model} created"
      fi
      announce "✅ Model \"${model}\" and associated service deployed."
    fi

    unset LLMDBENCH_DEPLOY_CURRENT_MODEL
    unset LLMDBENCH_DEPLOY_CURRENT_MODEL_ID
    unset LLMDBENCH_DEPLOY_CURRENT_MODEL_ID_LABEL

    model_number=$((model_number + 1))
  done
  announce "✅ modelservice completed model deployment"

else
  announce "⏭️ Environment types are \"${LLMDBENCH_DEPLOY_METHODS}\". Skipping this step."
fi
