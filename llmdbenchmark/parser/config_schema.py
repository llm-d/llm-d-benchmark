"""Pydantic v2 config validation for the LLM-D benchmark rendering pipeline.

Validates the merged config dict (defaults + scenario) after resolvers run,
before Jinja template rendering.  Validation is non-blocking: errors are
collected and returned as warning strings, never raised as exceptions.

Phase 1 covers the most commonly overridden and error-prone sections:
model, decode, prefill, vllmCommon, harness, and top-level parallelism.

The root model uses ``extra="allow"`` so that unmodeled top-level keys
pass through without error.  Nested section models use ``extra="forbid"``
to catch typos within modeled sections.

Fields do not carry default values -- ``defaults.yaml`` is the single source
of truth for defaults.  The schema only defines types and constraints.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ---------------------------------------------------------------------------
# Shared ConfigDict presets
# ---------------------------------------------------------------------------

STRICT_CONFIG = ConfigDict(
    extra="forbid",
    validate_assignment=True,
    str_strip_whitespace=True,
)

LENIENT_CONFIG = ConfigDict(
    extra="allow",
    validate_assignment=True,
    str_strip_whitespace=True,
)

# ---------------------------------------------------------------------------
# Shared / reusable sub-models
# ---------------------------------------------------------------------------


class ParallelismConfig(BaseModel):
    """Parallelism settings (used by decode, prefill, standalone, and top-level)."""

    model_config = STRICT_CONFIG

    data: int = Field(ge=0)
    dataLocal: int = Field(ge=0)
    tensor: int = Field(ge=0)
    workers: int = Field(ge=0)


class ResourceQuantities(BaseModel):
    """Container resource limits/requests.

    Uses ``extra="allow"`` because arbitrary accelerator keys
    (``nvidia.com/gpu``, ``ibm.com/spyre_vf``, etc.) are valid here.
    """

    model_config = LENIENT_CONFIG

    memory: str | int
    cpu: str | int


class ResourcesConfig(BaseModel):
    """Resource configuration (limits + requests)."""

    model_config = STRICT_CONFIG

    limits: ResourceQuantities
    requests: ResourceQuantities


class ProbeConfig(BaseModel):
    """Health probe (startup / liveness / readiness)."""

    model_config = STRICT_CONFIG

    path: str | None = None
    failureThreshold: int = Field(ge=1)
    initialDelaySeconds: int | None = Field(default=None, ge=0)
    periodSeconds: int = Field(ge=1)
    timeoutSeconds: int | None = Field(default=None, ge=1)


class ProbesConfig(BaseModel):
    """Container probe configuration."""

    model_config = STRICT_CONFIG

    startup: ProbeConfig
    liveness: ProbeConfig
    readiness: ProbeConfig


class AcceleratorTypeConfig(BaseModel):
    """Node selector for accelerator type (GPU label matching)."""

    model_config = STRICT_CONFIG

    labelKey: str
    labelValue: str
    labelValues: list[str] | None = None


class AcceleratorConfig(BaseModel):
    """Scenario-level accelerator override (count / resourceName).

    Distinct from ``AcceleratorTypeConfig`` which is for node selection.
    Scenarios use this for resource quantity overrides.
    """

    model_config = LENIENT_CONFIG

    count: int | str | None = None
    resourceName: str | None = None
    memory: str | None = None


class PodMonitorConfig(BaseModel):
    """PodMonitor configuration for Prometheus scraping."""

    model_config = STRICT_CONFIG

    enabled: bool
    portName: str
    path: str
    interval: str
    scrapeTimeout: str | None = None
    labels: dict[str, str]
    annotations: dict[str, str]
    relabelings: list[Any]
    metricRelabelings: list[Any]


class DeploymentMonitoringConfig(BaseModel):
    """Monitoring block for decode/prefill deployments."""

    model_config = STRICT_CONFIG

    podmonitor: PodMonitorConfig


class AutoscalingConfig(BaseModel):
    """Horizontal pod autoscaler configuration."""

    model_config = STRICT_CONFIG

    enabled: bool
    minReplicas: int | None = None
    maxReplicas: int | None = None


# ---------------------------------------------------------------------------
# vLLM serve config (inside decode/prefill)
# ---------------------------------------------------------------------------


class VllmServeConfig(BaseModel):
    """vLLM configuration inside decode/prefill sections."""

    model_config = STRICT_CONFIG

    port: int | None = None
    servicePort: int | None = None
    workerMultiprocMethod: str
    loggingLevel: str
    imagePullPolicy: str | None = None
    customCommand: str | None = None
    customPreprocessCommand: str | None = None
    additionalFlags: list[str]
    modelCommand: str | None = None


# ---------------------------------------------------------------------------
# Deployment base (shared by decode/prefill)
# ---------------------------------------------------------------------------


class DeploymentBaseConfig(BaseModel):
    """Shared structure for decode and prefill deployment sections."""

    model_config = STRICT_CONFIG

    enabled: bool
    replicas: int = Field(ge=0)

    autoscaling: AutoscalingConfig
    nodeSelector: dict[str, str]
    schedulerName: str | None = None
    priorityClassName: str | None = None
    ephemeralStorage: str | None = None
    networkResource: str | None = None
    networkNr: str | None = None

    acceleratorType: AcceleratorTypeConfig
    accelerator: AcceleratorConfig | None = None

    parallelism: ParallelismConfig
    resources: ResourcesConfig
    shm: dict[str, str] | None = None
    probes: ProbesConfig
    vllm: VllmServeConfig

    mountModelVolume: bool
    additionalVolumeMounts: list[Any]
    additionalVolumes: list[Any]
    extraEnvVars: list[dict[str, Any]]
    extraContainerConfig: dict[str, Any]
    extraPodConfig: dict[str, Any]
    initContainers: list[Any]
    monitoring: DeploymentMonitoringConfig

    annotations: dict[str, str] | None = None

    hostIPC: bool | None = None
    hostPID: bool | None = None
    enableServiceLinks: bool | None = None
    terminationGracePeriodSeconds: int | None = None
    subGroupPolicy: dict[str, Any] | None = None
    subGroupExclusiveTopology: bool | None = None


class DecodeConfig(DeploymentBaseConfig):
    """Decode-specific configuration."""

    model_config = STRICT_CONFIG


class PrefillConfig(DeploymentBaseConfig):
    """Prefill-specific configuration."""

    model_config = STRICT_CONFIG


# ---------------------------------------------------------------------------
# vllmCommon
# ---------------------------------------------------------------------------


class KvTransferConfig(BaseModel):
    """KV cache transfer configuration (NIXL connector)."""

    model_config = STRICT_CONFIG

    enabled: bool
    connector: str
    role: str
    extraConfig: dict | None = None


class KvEventsConfig(BaseModel):
    """KV events configuration (for precise prefix cache aware routing)."""

    model_config = STRICT_CONFIG

    enabled: bool
    publisher: str
    port: int
    topicPrefix: str
    serviceName: str | None = None


class VllmFlagsConfig(BaseModel):
    """vLLM serve flags.

    Uses ``extra="allow"`` because new flags are frequently added to vLLM
    and scenarios may reference them before the schema is updated.
    """

    model_config = LENIENT_CONFIG

    enforceEager: bool | None = None
    disableLogRequests: bool | None = None
    disableUvicornAccessLog: bool | None = None
    allowLongMaxModelLen: str | None = None
    serverDevMode: str | None = None
    enableChunkedPrefill: bool | None = None
    maxNumBatchedTokens: int | None = None
    noPrefixCaching: bool | None = None
    enablePrefixCaching: bool | None = None
    enableAutoToolChoice: bool | None = None
    toolCallParser: str | None = None


class VllmCommonConfig(BaseModel):
    """Shared vLLM configuration applied to all deployment modes."""

    model_config = STRICT_CONFIG

    inferencePort: int
    host: str
    preprocessScript: str
    kvTransfer: KvTransferConfig
    kvEvents: KvEventsConfig
    priorityClassName: str
    pullSecret: str
    containerHome: str
    hfHome: str
    ephemeralStorageResource: str
    ephemeralStorage: str
    networkResource: str
    networkNr: str
    nixlSideChannelPort: str
    ucxTls: str
    ucxSockaddrTlsPriority: str
    flags: VllmFlagsConfig
    volumes: list[dict[str, Any]]
    volumeMounts: list[dict[str, Any]]

    shmMemory: str | None = None
    podScheduler: str | None = None


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """Model configuration."""

    model_config = STRICT_CONFIG

    name: str
    shortName: str
    path: str
    huggingfaceId: str
    size: str
    maxModelLen: int | str
    blockSize: int
    gpuMemoryUtilization: float = Field(ge=0, le=1)
    cacheBase: str

    maxNumSeq: int | None = None
    maxNumBatchedTokens: int | None = None


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class HarnessResourcesConfig(BaseModel):
    """Harness pod resource configuration."""

    model_config = STRICT_CONFIG

    cpu: int | str
    memory: str


class InferencePerfConfig(BaseModel):
    """Inference-perf harness configuration."""

    model_config = STRICT_CONFIG

    rayonNumThreads: int


class HarnessConfig(BaseModel):
    """Benchmark harness configuration."""

    model_config = STRICT_CONFIG

    name: str
    profile: str | None = None
    experimentProfile: str | None = None
    executable: str
    condaEnvName: str
    waitTimeout: int = Field(ge=0)
    loadParallelism: int = Field(ge=1)
    podLabel: str
    debug: bool
    resources: HarnessResourcesConfig
    output: str
    inferencePerf: InferencePerfConfig
    namespace: str | None = None
    pvcSize: str | None = None


# ---------------------------------------------------------------------------
# Root model
# ---------------------------------------------------------------------------


class BenchmarkConfig(BaseModel):
    """Root validation model for the merged config dict.

    Uses ``extra="allow"`` at the root level so that sections not yet
    modeled are accepted without error.  This enables incremental adoption --
    only the explicitly modeled sections below are validated with
    ``extra="forbid"``.
    """

    model_config = ConfigDict(extra="allow")

    model: ModelConfig
    decode: DecodeConfig
    prefill: PrefillConfig
    vllmCommon: VllmCommonConfig
    harness: HarnessConfig
    parallelism: ParallelismConfig | None = None

    # Scenario-level workspace directory (equivalent to LLMDBENCH_CONTROL_WORK_DIR).
    # Used as workspace fallback when --workspace is not specified on the CLI.
    workDir: str | None = None


# ---------------------------------------------------------------------------
# Validation entry point
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def validate_config(
    merged_values: dict[str, Any],
    render_logger: Any | None = None,
) -> list[str]:
    """Validate a merged config dict against the benchmark schema.

    This function is intentionally **non-blocking**: it never raises
    exceptions.  All validation issues are collected and returned as a
    list of human-readable warning strings.

    Parameters
    ----------
    merged_values:
        The fully-merged config dict (defaults + scenario, after resolvers).
    render_logger:
        Optional logger instance (with ``log_warning`` method) from the
        rendering pipeline.  Falls back to the module-level stdlib logger.

    Returns
    -------
    list[str]
        Validation warning messages.  Empty list means the config is valid
        (within the scope of modeled sections).
    """
    warnings: list[str] = []

    try:
        BenchmarkConfig.model_validate(merged_values)
    except ValidationError as exc:
        for error in exc.errors():
            field_path = ".".join(str(loc) for loc in error["loc"])
            msg = f"Config validation: {field_path} -- {error['msg']}"
            warnings.append(msg)
            if render_logger and hasattr(render_logger, "log_warning"):
                render_logger.log_warning(msg)
            else:
                logger.warning(msg)
    except Exception as exc:
        msg = f"Config validation unexpected error: {exc}"
        warnings.append(msg)
        if render_logger and hasattr(render_logger, "log_warning"):
            render_logger.log_warning(msg)
        else:
            logger.warning(msg)

    return warnings
