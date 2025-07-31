from enum import StrEnum, auto
import json
from operator import attrgetter
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
    args: dict[str, Any]
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
    MB_PER_S = 'MB/s'
    GB_PER_S = 'GB/s'
    TB_PER_S = 'TB/s'
    # Generation latency
    MS_PER_TOKEN = 'ms/token'
    # Power
    WATTS = "Watts"

# Lists of compatible units
units_quantity = [Units.COUNT]
units_portion = [Units.PERCENT, Units.FRACTION]
units_time = [Units.MS, Units.S]
units_memory = [Units.MB, Units.GB, Units.TB, Units.MIB, Units.GIB, Units.TIB]
units_bandwidth = [Units.MBIT_PER_S, Units.GBIT_PER_S, Units.TBIT_PER_S, Units.MB_PER_S, Units.GB_PER_S, Units.TB_PER_S]
units_gen_latency = [Units.MS_PER_TOKEN]
units_power = [Units.WATTS]


class Statistics(BaseModel):
    """Statistical information about a property."""

    units: Units
    mean: float
    median: Optional[float | int] = None
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
        if self.input_length.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.input_length.units}", must be one of: {' '.join(units_quantity)}')
        if self.output_length.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.output_length.units}", must be one of: {' '.join(units_quantity)}')
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
            raise ValueError(f'Invalid units "{self.time_to_first_token.units}", must be one of: {' '.join(units_time)}')
        if self.normalized_time_per_output_token and self.normalized_time_per_output_token.units not in units_gen_latency:
            raise ValueError(f'Invalid units "{self.normalized_time_per_output_token.units}", must be one of: {' '.join(units_gen_latency)}')
        if self.time_per_output_token and self.time_per_output_token.units not in units_gen_latency:
            raise ValueError(f'Invalid units "{self.time_per_output_token.units}", must be one of: {' '.join(units_gen_latency)}')
        if self.inter_token_latency and self.inter_token_latency.units not in units_gen_latency:
            raise ValueError(f'Invalid units "{self.inter_token_latency.units}", must be one of: {' '.join(units_gen_latency)}')
        if self.request_latency and self.request_latency.units not in units_time:
            raise ValueError(f'Invalid units "{self.request_latency.units}", must be one of: {' '.join(units_time)}')
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
            raise ValueError(f'Invalid units "{self.batch_size.units}", must be one of: {' '.join(units_quantity)}')
        if self.queue_size and self.queue_size.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.queue_size.units}", must be one of: {' '.join(units_quantity)}')
        if self.kv_cache_size and self.kv_cache_size.units not in units_quantity:
            raise ValueError(f'Invalid units "{self.kv_cache_size.units}", must be one of: {' '.join(units_quantity)}')
        return self


class MemoryMetrics(BaseModel):
    """Memory metrics."""

    consumption: Optional[Statistics] = None
    utilization: Optional[Statistics] = None
    bandwidth: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.consumption and self.consumption.units not in units_memory:
            raise ValueError(f'Invalid units "{self.consumption.units}", must be one of: {' '.join(units_memory)}')
        if self.utilization and self.utilization.units not in units_portion:
            raise ValueError(f'Invalid units "{self.utilization.units}", must be one of: {' '.join(units_portion)}')
        if self.bandwidth and self.bandwidth.units not in units_bandwidth:
            raise ValueError(f'Invalid units "{self.bandwidth.units}", must be one of: {' '.join(units_bandwidth)}')
        return self


class ComputeMetrics(BaseModel):
    """Compute metrics."""

    utilization: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.utilization.units not in units_portion:
            raise ValueError(f'Invalid units "{self.utilization.units}", must be one of: {' '.join(units_portion)}')
        return self


class AcceleratorMetrics(BaseModel):
    """Accelerator hardware metrics."""

    memory: Optional[MemoryMetrics] = None
    compute: Optional[ComputeMetrics] = None
    power: Optional[Statistics] = None

    @model_validator(mode='after')
    def check_units(self):
        if self.power and self.power.units not in units_power:
            raise ValueError(f'Invalid units "{self.power.units}", must be one of: {' '.join(units_power)}')
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
        """Convert BenchmarkRun to dict.

        Returns:
            dict: Defined fields of BenchmarkRun.
        """
        return self.model_dump(
            mode="json",
            exclude_unset=False,
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


def create_from_str(yaml_str: str) -> BenchmarkRun:
    """
    Create a BenchmarkRun instance from a JSON/YAML string.

    Args:
        yaml_str (str): JSON/YAML string to import.

    Returns:
        BenchmarkRun: Instance with values from string.
    """
    return BenchmarkRun(**yaml.safe_load(yaml_str))



# If this is executed directly, print JSON schema.
if __name__ == "__main__":
    print(make_json_schema())


    # Demo code, creating a BenchmarkRun object directly
    # br = BenchmarkRun(**{
    #     "scenario": {
    #         "model": {"name": "deepseek-ai/DeepSeek-R1-0528"},
    #         "load": {"name": WorkloadGenerator.INFERENCE_PERF},
    #         "host": {
    #             "accelerator": [{"model": "H100", "memory": 80, "count": 3}, {"model": "H100", "memory": 80, "count": 3}],
    #             "type": ["prefill", "decode"]
    #         },
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


    # Demo code, creating a BenchmarkRun from a YAML string
#     example_yaml = """
# version: '0.1' # Apply a version that updates with schema changes
# scenario: # This section provides the specific environment and workload
#   description: This is a heterogeneous accelerator setup with two lora adapters
#   host:
#     type:
#       - prefill
#       - decode
#       - decode
#     accelerator: # This is heterogeneous across prefill and decode, with 1 prefill and 2 decode
#       - model: H100 # Prefill
#         memory: 80
#         count: 1
#         parallelism:
#           dp: 1
#           tp: 1
#           pp: 1
#           ep: 1
#       - model: H100 # First decode
#         memory: 80
#         count: 8
#         parallelism:
#           dp: 1
#           tp: 8
#           pp: 1
#           ep: 8
#       - model: H100 # Second decode
#         memory: 80
#         count: 8
#         parallelism:
#           dp: 1
#           tp: 8
#           pp: 1
#           ep: 8
#   platform:
#     engine: # This list correlates 1:1 with the items listed in scenario.host.accelerator
#       - name: vllm # Prefill
#         version: 0.9.0.1
#         args:
#           "--dtype": fp16
#           "--tensor-parallel-size": 1
#           "--pipeline-parallel-size": 1
#           "--enable-expert-parallel": true
#           "--data-parallel-size": 1
#           "--data-parallel-size-local": 1
#       - name: vllm # First decode
#         version: 0.9.0.1
#         args:
#           "--dtype": fp16
#           "--tensor-parallel-size": 8
#           "--pipeline-parallel-size": 1
#           "--enable-expert-parallel": true
#           "--data-parallel-size": 3
#           "--data-parallel-size-local": 1
#           "--data-parallel-address": 10.12.33.212
#           "--data-parallel-rpc-port": 5555
#           "--data-parallel-start-rank": 1
#       - name: vllm # Second decode
#         version: 0.9.0.1
#         args:
#           "--dtype": fp16
#           "--tensor-parallel-size": 8
#           "--pipeline-parallel-size": 1
#           "--enable-expert-parallel": true
#           "--data-parallel-size": 3
#           "--data-parallel-size-local": 1
#           "--data-parallel-address": 10.12.33.212
#           "--data-parallel-rpc-port": 5555
#           "--data-parallel-start-rank": 2
#   model:
#     name: deepseek-ai/DeepSeek-R1-0528
#     quantization: fp16
#     adapters:
#     - lora: sql_adapter
#     - lora: golang_adapter
#   load: # Unsure about best format here... in principle this should contain enough information to execute a load generator
#     name: inference-perf
#     type: long-input
#     args:
#       qps_values: 1.34
#       num_users_warmpup: 20
#       num_users: 15
#       num_rounds: 20
#       system_prompt: 1000
#       chat_history: 20000
#       answer_len: 100
#       test_duration: 100
#       use_chat_completions: false
# metrics: # These are the aggregate results from benchmarking
#   time:
#     duration: 16.531641244888306
#     start: 1749570583.5714512 # UTC seconds from epoch
#     stop: 1749570580.1030924
#   requests:
#     total: 32
#     failures: 0
#     input_length:
#       units: count
#       mean: 628.606060606061
#       stddev: 19.8353456345
#       min: 4
#       p10: 11
#       p50: 364
#       p90: 2427
#       max: 3836
#     output_length:
#       units: count
#       mean: 31.7878787878788
#       stddev: 19.8353456345
#       min: 30
#       p10: 31
#       p50: 32
#       p90: 32
#       max: 32
#   latency:
#     request_latency:
#       units: ms
#       mean: 3.31325431142327
#       stddev: 0.00198353456345
#       min: 1.62129471905064
#       p10: 1.67609986825846
#       p50: 2.11507539497688
#       p90: 5.94717199734878
#       max: 6.30658466403838
#     normalized_time_per_output_token:
#       units: ms/token
#       mean: 0.104340420636009
#       stddev: 0.00198353456345
#       min: 0.0506654599703325
#       p10: 0.0523781208830769
#       p50: 0.0670631669655753
#       p90: 0.189047570470012
#       max: 0.20343821496898
#     time_per_output_token:
#       units: ms/token
#       mean: 0.0836929455635872
#       stddev: 0.00198353456345
#       min: 0.0517028436646797
#       p10: 0.0530815053513894
#       p50: 0.0611870964678625
#       p90: 0.152292036800645
#       max: 0.17837208439984
#     time_to_first_token:
#       units: ms
#       mean: 0.800974442732916
#       stddev: 0.00198353456345
#       min: 0.0625283779809251
#       p10: 0.072068731742911
#       p50: 0.203539535985328
#       p90: 2.26959549135063
#       max: 4.46773961000145
#     inter_token_latency:
#       units: ms/token
#       mean: 0.0836929455635872
#       stddev: 0.00198353456345
#       min: 7.129972800612e-06
#       p10: 0.0534287681337446
#       p50: 0.0591336835059337
#       p90: 0.084046097996179
#       max: 0.614475268055685
#   throughput:
#     input_tokens_per_sec: 643.576644186323
#     output_tokens_per_sec: 32.544923821416
#     total_tokens_per_sec: 676.121568007739
#     requests_per_sec: 1.0238155253639
#   service: # These are metrics about the inference service
#     batch_size:
#       units: count
#       mean: 234.23049
#       stddev: 34.12342
#       min: 123
#       p10: 143
#       p50: 533
#       p90: 625
#       max: 753
#     queue_size:
#       units: count
#       mean: 234.12451
#       stddev: 34.56737
#       min: 123
#       p10: 143
#       p50: 533
#       p90: 625
#       max: 753
#     kv_cache_size:
#       units: count
#       mean: 2194993.253
#       stddev: 2342.3456
#       min: 1194345
#       p10: 1394456
#       p50: 2404751
#       p90: 2534437
#       max: 2554393
#   resources: # These are hardware level metrics
#     accelerator: # This list correlates 1:1 with the items listed in scenario.host.accelerator
#       - memory: # This corresponds to the prefill pod
#           consumption:
#             units: MB
#             mean: 2194993.2346
#             stddev: 2342.4568
#             min: 1194345
#             p10: 1394456
#             p50: 2404751
#             p90: 2534437
#             max: 2554393
#           utilization:
#             units: percent
#             mean: 80.235
#             stddev: 32.1
#             min: 40.3
#             p10: 44.4
#             p50: 71.3
#             p90: 97.1
#             max: 99.2
#           bandwidth:
#             units: MB/s
#             mean: 21993.2346
#             stddev: 22.4568
#             min: 19445.2347
#             p10: 13456.5367
#             p50: 24051.2456
#             p90: 24437.4582
#             max: 25543.3457
#         compute:
#           utilization:
#             units: percent
#             mean: 40.56
#             stddev: 12.15
#             min: 20.3
#             p10: 24.4
#             p50: 31.3
#             p90: 47.1
#             max: 49.2
#         power:
#           units: Watts
#           mean: 410.02
#           stddev: 170.1
#           min: 201.3
#           p10: 243.4
#           p50: 314.3
#           p90: 475.1
#           max: 497.2
#       - memory: # This corresponds to the first decode pod
#           consumption:
#             units: MB
#             mean: 2194993.2346
#           utilization:
#             units: percent
#             mean: 80.235
#           bandwidth:
#             units: MB/s
#             mean: 21993.2346
#         compute:
#           utilization:
#             units: percent
#             mean: 40.56
#         power:
#           units: Watts
#           mean: 410.02
#       - memory: # This corresponds to the second decode pod
#           consumption:
#             units: MB
#             mean: 2194993.2346
#           utilization:
#             units: percent
#             mean: 80.235
#           bandwidth:
#             units: MB/s
#             mean: 21993.2346
#         compute:
#           utilization:
#             units: percent
#             mean: 40.56
#         power:
#           units: Watts
#           mean: 410.02
# """
#     create_from_str(example_yaml).print_yaml()
