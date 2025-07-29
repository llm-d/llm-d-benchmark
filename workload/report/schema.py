from enum import StrEnum, auto
import json
from typing import Optional, Any

from pydantic import BaseModel, model_validator
import yaml


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
    memory: float | int
    """Amount of memory in one accelerator, in GB."""
    count: int
    """Number of accelerators."""
    parallelism: Optional[Parallelism] = None
    """Parallelism configuration used."""
    metadata: Optional[Any] = None


class Host(BaseModel):
    """Host hardware details."""

    accelerator: list[HostAccelerator]
    metadata: Optional[Any] = None


class EngineDetails(BaseModel):
    """Inverence engine details."""

    name: str
    version: Optional[str] = None
    args: dict[str, Any]
    metadata: Optional[Any] = None


class Platform(BaseModel):
    """Software platform details endompassing all inference engines."""

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
    """

    FMPERF = auto()
    GUIDELLM = auto()
    INFERENCE_PERF = 'inference-perf'
    VLLM_BENCHMARK = 'vllm-benchmark'


class Load(BaseModel):
    """Workload for benchmark run."""

    name: WorkloadGenerator
    """Workload generator"""
    type: Optional[str] = None
    args: Optional[dict[str, Any]] = None
    metadata: Optional[Any] = None


class Scenario(BaseModel):
    """System configuration and workload details for benchmark run."""

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
        MS_PER_TOKEN: str
            Milliseconds per token
        WATTS: str
            Watts
    """

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
    MB_PER_S = 'MB/s'
    GB_PER_S = 'GB/s'
    TB_PER_S = 'TB/s'
    MS_PER_TOKEN = 'ms/token'
    # Power
    WATTS = "Watts"

# Lists of compatible units
units_portion = [Units.PERCENT, Units.FRACTION]
units_time = [Units.MS, Units.S]
units_memory = [Units.MB, Units.GB, Units.TB, Units.MIB, Units.GIB, Units.TIB]
units_bandwidth = [Units.MBIT_PER_S, Units.GBIT_PER_S, Units.TBIT_PER_S, Units.MB_PER_S, Units.GB_PER_S, Units.TB_PER_S]
units_power = [Units.WATTS]


class Statistics(BaseModel):
    """Statistical information about a property."""

    units: Units
    mean: float
    stddev: Optional[float] = None
    min: Optional[float | int] = None
    p10: Optional[float | int] = None
    p50: Optional[float | int] = None
    p90: Optional[float | int] = None
    p95: Optional[float | int] = None
    p99: Optional[float | int] = None
    max: Optional[float | int] = None


class Requests(BaseModel):
    """Request statistics."""

    total: int
    """Total number of requests sent."""
    failures: Optional[int] = None
    """Number of requests which did not result in a completed response."""
    input_length: Statistics
    """Input sequence length."""
    output_length: Statistics
    """Output sequence length."""

    @model_validator(mode='after')
    def check_units(self):
        if self.input_length.units not in [Units.COUNT]:
            raise ValueError(f'Invalid units "{self.input_length.units}", must be one of: {' '.join([Units.COUNT])}')
        if self.output_length.units not in [Units.COUNT]:
            raise ValueError(f'Invalid units "{self.output_length.units}", must be one of {' '.join([Units.COUNT])}')
        return self


class Latency(BaseModel):
    """Response latency performance metrics."""

    request_latency: Optional[Statistics] = None
    normalized_time_per_output_token: Optional[Statistics] = None
    time_per_output_token: Optional[Statistics] = None
    time_to_first_token: Statistics
    inter_token_latency: Optional[Statistics] = None
    e2e: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.request_latency and self.request_latency.units not in units_time:
            raise ValueError(f'Invalid units "{self.request_latency.units}", must be one of {' '.join(units_time)}')
        if self.normalized_time_per_output_token and self.normalized_time_per_output_token.units not in units_time:
            raise ValueError(f'Invalid units "{self.normalized_time_per_output_token.units}", must be one of {' '.join(units_time)}')
        if self.time_per_output_token and self.time_per_output_token.units not in units_time:
            raise ValueError(f'Invalid units "{self.time_per_output_token.units}", must be one of {' '.join(units_time)}')
        if self.time_to_first_token.units not in units_time:
            raise ValueError(f'Invalid units "{self.time_to_first_token.units}", must be one of {' '.join(units_time)}')
        if self.inter_token_latency and self.inter_token_latency.units not in units_time:
            raise ValueError(f'Invalid units "{self.inter_token_latency.units}", must be one of {' '.join(units_time)}')
        if self.e2e and self.e2e.units not in units_time:
            raise ValueError(f'Invalid units "{self.e2e and self.e2e.units}", must be one of {' '.join(units_time)}')
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
        if self.batch_size and self.batch_size.units not in [Units.COUNT]:
            raise ValueError(f'Invalid units "{self.batch_size.units}", must be one of {' '.join([Units.COUNT])}')
        if self.queue_size and self.queue_size.units not in [Units.COUNT]:
            raise ValueError(f'Invalid units "{self.queue_size.units}", must be one of {' '.join([Units.COUNT])}')
        if self.kv_cache_size and self.kv_cache_size.units not in [Units.COUNT]:
            raise ValueError(f'Invalid units "{self.kv_cache_size.units}", must be one of {' '.join([Units.COUNT])}')
        return self


class MemoryMetrics(BaseModel):
    """Memory metrics."""

    consumption: Optional[Statistics] = None
    utilization: Optional[Statistics] = None
    bandwidth: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.consumption and self.consumption.units not in units_memory:
            raise ValueError(f'Invalid units "{self.consumption.units}", must be one of {' '.join(units_memory)}')
        if self.utilization and self.utilization.units not in units_portion:
            raise ValueError(f'Invalid units "{self.utilization.units}", must be one of {' '.join(units_portion)}')
        if self.bandwidth and self.bandwidth.units not in units_bandwidth:
            raise ValueError(f'Invalid units "{self.bandwidth.units}", must be one of {' '.join(units_bandwidth)}')
        return self


class ComputeMetrics(BaseModel):
    """Memory metrics."""

    utilization: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.utilization.units not in units_portion:
            raise ValueError(f'Invalid units "{self.utilization.units}", must be one of {' '.join(units_portion)}')
        return self


class AcceleratorMetrics(BaseModel):
    """Accelerator hardware metrics."""

    memory: Optional[MemoryMetrics] = None
    compute: Optional[ComputeMetrics] = None
    power: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.power and self.power.units not in units_power:
            raise ValueError(f'Invalid units "{self.power.units}", must be one of {' '.join(units_power)}')
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


class BenchmarkRun(BaseModel):
    """Base class for a benchmark run."""

    version: str = '0.1'
    """Version of the schema."""
    scenario: Scenario
    metrics: Metrics
    metadata: Optional[Any] = None

    @model_validator(mode='after')
    def check_corresponding_lengths(self):
        """Ensure the lengths of the following match (if present):
            - scenario.host.accelerator
            - scenario.platform.engine
            - metrics.resources.accelerator
        """
        sha, spe, mra = None, None, None
        if self.scenario.host:
            if self.scenario.host.accelerator:
                sha = len(self.scenario.host.accelerator)
        if self.scenario.platform:
            if self.scenario.platform.engine:
                spe = len(self.scenario.platform.engine)
                if sha and sha != spe:
                    raise ValueError(
                        f'Length of "scenario.platform.engine" ({spe}) must match "scenario.host.accelerator" ({sha})'
                    )
        if self.metrics.resources:
            if self.metrics.resources.accelerator:
                mra = len(self.metrics.resources.accelerator)
                if sha and sha != mra:
                    raise ValueError(
                        f'Length of "metrics.resources.accelerator" ({mra}) must match "scenario.host.accelerator" ({sha})'
                    )
                if spe and spe != mra:
                    raise ValueError(
                        f'Length of "metrics.resources.accelerator" ({mra}) must match "scenario.platform.engine" ({spe})'
                    )
        return self

    def dump(self) -> dict[str, Any]:
        """Convert BenchmarkRun to dict.

        Returns:
            dict: Defined fields of BenchmarkRun.
        """
        return self.model_dump(
            mode="json",
            exclude_unset=True,
            by_alias=True,
        )

    def print_json(self) -> None:
        """Print BenchmarkRun as JSON."""
        print(
            json.dumps(self.dump(), indent=2)
        )

    def print_yaml(self) -> None:
        """Print BenchmarkRun as YAML."""
        print(
            yaml.dump(self.dump(), indent=2)
        )


def make_json_schema() -> str:
    """
    Create a JSON schema for the benchmark run.

    Returns:
        str: JSON schema of benchmark run.
    """
    return json.dumps(BenchmarkRun.model_json_schema(), indent=2)


# If this is executed directly, print JSON schema.
if __name__ == "__main__":
    print(make_json_schema())

    # Demo code
    # br = BenchmarkRun(**{
    #     "scenario": {
    #         "model": {"name": "deepseek-ai/DeepSeek-R1-0528"},
    #         "load": {"name": WorkloadGenerator.INFERENCE_PERF},
    #         "host": {"accelerator": [{"model": "H100", "memory": 80, "count": 3}, {"model": "H100", "memory": 80, "count": 3}]},
    #         "platform": {"engine": [{"name": "vllm", "args": {}}, {"name": "vllm", "args": {}}]},
    #     },
    #     "metrics": {
    #         "time": {"duration": 10.3},
    #         "requests": {
    #             "total": 58,
    #             "input_length": {
    #                 "units": Units.COUNT,
    #                 "mean": 1000,
    #             },
    #             "output_length": {
    #                 "units": Units.COUNT,
    #                 "mean": 20000,
    #             },
    #         },
    #         "latency": {
    #             "time_to_first_token": {
    #                 "units": Units.MS,
    #                 "mean": 3.4,
	# 			},
    #         },
    #         "throughput": {"total_tokens_per_sec": 30.4},
    #         "resources": {"accelerator": [{"power": {"units": Units.WATTS, "mean": 9.3}}, {"power": {"units": Units.WATTS, "mean": 9.3}}]},
    #     },
    # })
    # br.print_yaml()
