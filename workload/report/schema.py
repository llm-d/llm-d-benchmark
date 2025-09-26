#!/usr/bin/env python3

from enum import StrEnum, auto
import json
from operator import attrgetter
from typing import Optional, Any

from pydantic import BaseModel, model_validator
import yaml


# BenchmarkReport schema version
VERSION = '0.1'

class Parallelism(BaseModel):
    """Accelerator parallelism details."""

    dp: int = 1
    """Data parallelism level."""
    tp: int = 1
    """Tensor parallelism level."""
    pp: int = 1
    """Pipeline parallelism level."""
    ep: int = 1
    """Expert parallelism level."""


class HostAccelerator(BaseModel):
    """Host accelerator details."""

    model: str
    """Accelerator model."""
    memory: Optional[float | int] = None
    """Amount of memory in one accelerator, in GB."""
    count: int
    """Number of accelerators."""
    parallelism: Optional[Parallelism] = None
    """Parallelism configuration used."""
    metadata: Optional[Any] = None


class HostType(StrEnum):
    """
    Enumeration of supported workload generators

    Attributes
        REPLICA: str
            Standard instance of an inference service
        PREFILL: str
            Prefill instance of an inference service
        DECODE: str
            Decode instance of an inference service
    """

    REPLICA = auto()
    PREFILL = auto()
    DECODE = auto()


class Host(BaseModel):
    """Host hardware details."""

    accelerator: list[HostAccelerator]
    type: list[HostType]
    metadata: Optional[Any] = None

    @model_validator(mode='after')
    def check_types(self):
        """Types must be either all 'replica' or a mix of 'prefill' and 'decode'."""
        if len(self.type) <= 1:
            # Nothing to compare
            return self
        type_ref = self.type[0]
        if type_ref == HostType.REPLICA:
            if HostType.DECODE in self.type:
                raise ValueError(f'Cannot mix "replica" with "prefill"/"decode" types.')
            if HostType.PREFILL in self.type:
                raise ValueError(f'Cannot mix "replica" with "prefill"/"decode" types.')
        else:
            if HostType.REPLICA in self.type:
                raise ValueError(f'Cannot mix "replica" with "prefill"/"decode" types.')
        return self


class EngineDetails(BaseModel):
    """Inference engine details."""

    name: str
    version: Optional[str] = None
    args: Optional[dict[str, Any]] = {}
    metadata: Optional[Any] = None


class Platform(BaseModel):
    """Software platform details encompassing all inference engines."""

    engine: list[EngineDetails]
    """Details on inference engines, list corresponds 1:1 with scenario.host.accelerator."""
    metadata: Optional[Any] = None


class Model(BaseModel):
    """AI model details."""
    name: str
    quantization: Optional[str] = None
    adapters: Optional[list[dict[str, str]]] = None
    metadata: Optional[Any] = None


class WorkloadGenerator(StrEnum):
    """
    Enumeration of supported workload generators

    Attributes
        FMPERF: str
            fmperf
        GUIDELLM: str
            GuideLLM
        INFERENCE_PERF: str
            Inference Perf
        VLLM_BENCHMARK: str
            benchmark_serving from vLLM
        NOP: str
            vLLM Load times
    """

    FMPERF = auto()
    GUIDELLM = auto()
    INFERENCE_PERF = 'inference-perf'
    VLLM_BENCHMARK = 'vllm-benchmark'
    NOP = 'nop'


class Load(BaseModel):
    """Workload for benchmark run."""

    name: WorkloadGenerator
    """Workload generator"""
    type: Optional[str] = None
    args: Optional[dict[str, Any]] = None
    metadata: Optional[Any] = None


class Scenario(BaseModel):
    """System configuration and workload details for benchmark."""

    description: Optional[str] = None
    host: Optional[Host] = None
    platform: Optional[Platform] = None
    model: Model
    load: Load
    metadata: Optional[Any] = None


class Time(BaseModel):
    """Timing details of benchmark run."""

    duration: float
    """Duration of benchmark run, in seconds."""
    start: Optional[float] = None
    """Start time of benchmark run, in seconds from Unix epoch."""
    stop: Optional[float] = None
    """End time of benchmark run, in seconds from Unix epoch."""
    metadata: Optional[Any] = None


class Units(StrEnum):
    """
    Enumeration of units

    Attributes
        COUNT: str
            Count
        MS: str
            Milliseconds
        S: str
            Seconds
        MB: str
            Megabytes
        GB: str
            Gigabytes
        TB: str
            Terabytes
        MIB: str
            Mebibytes
        GIB: str
            Gibibytes
        TIB: str
            Tebibytes
        MBIT_PER_S: str
            Megabbits per second
        GBIT_PER_S: str
            Gigabits per second
        TBIT_PER_S: str
            Terabits per second
        MB_PER_S: str
            Megabytes per second
        GB_PER_S: str
            Gigabytes per second
        TB_PER_S: str
            Terabytes per second
        GIB_PER_S: str
            GiB per second
        MS_PER_TOKEN: str
            Milliseconds per token
        S_PER_TOKEN: str
            Seconds per token
        WATTS: str
            Watts
    """

    # Quantity
    COUNT = auto()
    # Portion
    PERCENT = auto()
    FRACTION = auto()
    # Time
    MS = auto()
    S = auto()
    # Memory
    MB = 'MB'
    GB = 'GB'
    TB = 'TB'
    MIB = 'MiB'
    GIB = 'GiB'
    TIB = 'TiB'
    # Bandwidth
    MBIT_PER_S = 'Mbit/s'
    GBIT_PER_S = 'Gbit/s'
    TBIT_PER_S = 'Tbit/s'
    GIB_PER_S = "GiB/s"

    MB_PER_S = 'MB/s'
    GB_PER_S = 'GB/s'
    TB_PER_S = 'TB/s'
    # Generation latency
    MS_PER_TOKEN = 'ms/token'
    S_PER_TOKEN = 's/token'
    # Power
    WATTS = "Watts"

# Lists of compatible units
units_quantity = [Units.COUNT]
units_portion = [Units.PERCENT, Units.FRACTION]
units_time = [Units.MS, Units.S]
units_memory = [Units.MB, Units.GB, Units.TB, Units.MIB, Units.GIB, Units.TIB]
units_bandwidth = [Units.MBIT_PER_S, Units.GBIT_PER_S, Units.TBIT_PER_S, Units.MB_PER_S, Units.GB_PER_S, Units.TB_PER_S]
units_gen_latency = [Units.MS_PER_TOKEN, Units.S_PER_TOKEN]
units_power = [Units.WATTS]


class Statistics(BaseModel):
    """Statistical information about a property."""

    units: Units
    mean: float
    mode: Optional[float | int] = None
    stddev: Optional[float] = None
    min: Optional[float | int] = None
    p0p1: Optional[float | int] = None
    p1: Optional[float | int] = None
    p5: Optional[float | int] = None
    p10: Optional[float | int] = None
    p25: Optional[float | int] = None
    p50: Optional[float | int] = None # This is the same as median
    p75: Optional[float | int] = None
    p90: Optional[float | int] = None
    p95: Optional[float | int] = None
    p99: Optional[float | int] = None
    p99p9: Optional[float | int] = None
    max: Optional[float | int] = None


class Requests(BaseModel):
    """Request statistics."""

    total: int
    """Total number of requests sent."""
    failures: Optional[int] = None
    """Number of requests which responded with an error."""
    incomplete: Optional[int] = None
    """Number of requests which were not completed."""
    input_length: Statistics
    """Input sequence length."""
    output_length: Statistics
    """Output sequence length."""

    @model_validator(mode='after')
    def check_units(self):
        if self.input_length.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.input_length.units}", must be one of: {" ".join(units_quantity)}')
        if self.output_length.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.output_length.units}", must be one of: {" ".join(units_quantity)}')
        return self


class Latency(BaseModel):
    """Response latency performance metrics."""

    time_to_first_token: Statistics
    """Time to generate the first token (TTFT)."""
    normalized_time_per_output_token: Optional[Statistics] = None
    """Typical time to generate an output token, including first (NTPOT)."""
    # NOTE: TPOT and ITL can be terms for the same quantity, but can also have
    # different meanings within a tool. Care must be taken when choosing which
    # quantity to use, especially when comparing results across different tools.
    #
    # From GKE
    # https://cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/inference
    # TPOT is calculated across the entire request
    # TPOT = (request_latency - time_to_first_token) / (total_output_tokens - 1)
    # ITL is measured between consecutive output tokens, and those results
    # aggregated to produce statistics.
    #
    # vLLM's benchmarking tools
    # https://github.com/vllm-project/vllm/issues/6531#issuecomment-2684695288
    # Obtaining TPOT statistics appears consistent with GKE definition, but
    # ITL is calculated across multiple requests.
    time_per_output_token: Optional[Statistics] = None
    """Time to generate an output token, excluding first (TPOT, may differ from ITL depending on tool)."""
    inter_token_latency: Optional[Statistics] = None
    """Latency between generated tokens, excluding first (ITL, may differ from TPOT depending on tool)."""
    request_latency: Optional[Statistics] = None
    """End-to-end request latency."""

    @model_validator(mode='after')
    def check_units(self):
        if self.time_to_first_token.units not in units_time:
            raise ValueError(f'Invalid units "{self.time_to_first_token.units}", must be one of: {" ".join(units_time)}')
        if self.normalized_time_per_output_token and self.normalized_time_per_output_token.units not in units_gen_latency:
            raise ValueError(f'Invalid units "{self.normalized_time_per_output_token.units}", must be one of: {" ".join(units_gen_latency)}')
        if self.time_per_output_token and self.time_per_output_token.units not in units_gen_latency:
            raise ValueError(f'Invalid units "{self.time_per_output_token.units}", must be one of: {" ".join(units_gen_latency)}')
        if self.inter_token_latency and self.inter_token_latency.units not in units_gen_latency:
            raise ValueError(f'Invalid units "{self.inter_token_latency.units}", must be one of: {" ".join(units_gen_latency)}')
        if self.request_latency and self.request_latency.units not in units_time:
            raise ValueError(f'Invalid units "{self.request_latency.units}", must be one of: {" ".join(units_time)}')
        return self


class Throughput(BaseModel):
    """Response throughput performance metrics."""

    input_tokens_per_sec: Optional[float] = None
    output_tokens_per_sec: Optional[float] = None
    total_tokens_per_sec: float
    requests_per_sec: Optional[float] = None


class Service(BaseModel):
    """Metrics about inference service."""

    batch_size: Optional[Statistics] = None
    queue_size: Optional[Statistics] = None
    kv_cache_size: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.batch_size and self.batch_size.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.batch_size.units}", must be one of: {" ".join(units_quantity)}')
        if self.queue_size and self.queue_size.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.queue_size.units}", must be one of: {" ".join(units_quantity)}')
        if self.kv_cache_size and self.kv_cache_size.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.kv_cache_size.units}", must be one of: {" ".join(units_quantity)}')
        return self


class MemoryMetrics(BaseModel):
    """Memory metrics."""

    consumption: Optional[Statistics] = None
    utilization: Optional[Statistics] = None
    bandwidth: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.consumption and self.consumption.units not in units_memory:
            raise ValueError(f'Invalid units "{self.consumption.units}", must be one of: {" ".join(units_memory)}')
        if self.utilization and self.utilization.units not in units_portion:
            raise ValueError(f'Invalid units "{self.utilization.units}", must be one of: {" ".join(units_portion)}')
        if self.bandwidth and self.bandwidth.units not in units_bandwidth:
            raise ValueError(f'Invalid units "{self.bandwidth.units}", must be one of: {" ".join(units_bandwidth)}')
        return self


class ComputeMetrics(BaseModel):
    """Compute metrics."""

    utilization: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.utilization.units not in units_portion:
            raise ValueError(f'Invalid units "{self.utilization.units}", must be one of: {" ".join(units_portion)}')
        return self


class AcceleratorMetrics(BaseModel):
    """Accelerator hardware metrics."""

    memory: Optional[MemoryMetrics] = None
    compute: Optional[ComputeMetrics] = None
    power: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.power and self.power.units not in units_power:
            raise ValueError(f'Invalid units "{self.power.units}", must be one of: {" ".join(units_power)}')
        return self


class ResourceMetrics(BaseModel):
    """Hardware resource metrics."""

    accelerator: list[AcceleratorMetrics]
    """Accelerator metrics, list corresponds 1:1 with scenario.host.accelerator."""


class Metrics(BaseModel):
    """Aggregate results from benchmarking run."""

    time: Time
    requests: Requests
    latency: Latency
    throughput: Throughput
    service: Optional[Service] = None
    resources: Optional[ResourceMetrics] = None
    description: Optional[str] = None
    metadata: Optional[Any] = None


class BenchmarkReport(BaseModel):
    """Base class for a benchmark report."""

    version: str = VERSION
    """Version of the schema."""
    scenario: Scenario
    metrics: Metrics
    metadata: Optional[Any] = None

    @model_validator(mode='after')
    def check_version(self):
        """Ensure version is compatible."""
        if self.version != VERSION:
            raise ValueError(f'Invalid version "{self.version}", must be "{VERSION}".')
        return self

    @model_validator(mode='after')
    def check_corresponding_lengths(self):
        """Ensure the lengths of the following match (if present):
            - scenario.host.accelerator
            - scenario.host.type
            - scenario.platform.engine
            - metrics.resources.accelerator
        """
        entity_lengths = {
            "scenario.host.accelerator": None,
            "scenario.host.type": None,
            "scenario.platform.engine": None,
            "metrics.resources.accelerator": None,
        }

        # Get lengths for fields that are defined
        for entity in entity_lengths.copy():
            try:
                entity_lengths[entity] = len(attrgetter(entity)(self))
            except AttributeError:
                # This field is not defined, drop it
                entity_lengths.pop(entity)

        if len(entity_lengths) <= 1:
            # Nothing to compare
            return self

        # Compare lengths
        entity_ref = list(entity_lengths.keys())[0]
        length_ref = entity_lengths.pop(entity_ref)
        for entity, length in entity_lengths.items():
            if length != length_ref:
                raise ValueError(
                    f'Length of "{entity}" ({length}) must match "{entity_ref}" ({length_ref})'
                )
        return self

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkReport to dict.

        Returns:
            dict: Defined fields of BenchmarkReport.
        """
        return self.model_dump(
            mode="json",
            exclude_none=True,
            by_alias=True,
        )

    def export_json(self, filename) -> None:
        """Save BenchmarkReport to JSON file.

        Args:
            filename: File to save BenchmarkReport to.
        """
        with open(filename, 'w') as file:
            json.dump(self.dump(), file, indent=2)

    def export_yaml(self, filename) -> None:
        """Save BenchmarkReport to YAML file.

        Args:
            filename: File to save BenchmarkReport to.
        """
        with open(filename, 'w') as file:
            yaml.dump(self.dump(), file, indent=2)

    def print_json(self) -> None:
        """Print BenchmarkReport as JSON."""
        print(
            json.dumps(self.dump(), indent=2)
        )

    def print_yaml(self) -> None:
        """Print BenchmarkReport as YAML."""
        print(
            yaml.dump(self.dump(), indent=2)
        )


def make_json_schema() -> str:
    """
    Create a JSON schema for the benchmark report.

    Returns:
        str: JSON schema of benchmark report.
    """
    return json.dumps(BenchmarkReport.model_json_schema(), indent=2)


def create_from_str(yaml_str: str) -> BenchmarkReport:
    """
    Create a BenchmarkReport instance from a JSON/YAML string.

    Args:
        yaml_str (str): JSON/YAML string to import.

    Returns:
        BenchmarkReport: Instance with values from string.
    """
    return BenchmarkReport(**yaml.safe_load(yaml_str))

# If this is executed directly, print JSON schema.
if __name__ == "__main__":
    print(make_json_schema())
