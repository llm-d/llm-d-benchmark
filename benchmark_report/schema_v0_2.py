"""
Benchmark report v0.2
"""

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .base import BenchmarkReport
from .schema_v0_2_components import *


# BenchmarkReport schema version
VERSION = '0.2'

# Default model_config to apply to Pydantic classes
MODEL_CONFIG = ConfigDict(
    extra="forbid", # Do not allow fields that are not part of this schema
    use_attribute_docstrings=True, # Use docstrings for JSON schema
    populate_by_name=False, # Must use alias name, not internal field name
    validate_assignment=True, # Validate field assignment after init
)

###############################################################################
# Units
###############################################################################

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
        TOKEN_PER_S: str
            Tokens per second
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
    # Generation throughput
    TOKEN_PER_S = 'token/s'
    # Request throughput
    QUERY_PER_S = 'qps'
    # Power
    WATTS = "Watts"

# Lists of compatible units
UNITS_QUANTITY = [Units.COUNT]
UNITS_PORTION = [Units.PERCENT, Units.FRACTION]
UNITS_TIME = [Units.MS, Units.S]
UNITS_MEMORY = [Units.MB, Units.GB, Units.TB, Units.MIB, Units.GIB, Units.TIB]
UNITS_BANDWIDTH = [Units.MBIT_PER_S, Units.GBIT_PER_S, Units.TBIT_PER_S, Units.MB_PER_S, Units.GB_PER_S, Units.TB_PER_S]
UNITS_GEN_LATENCY = [Units.MS_PER_TOKEN, Units.S_PER_TOKEN]
UNITS_GEN_THROUGHPUT = [Units.TOKEN_PER_S]
UNITS_REQUEST_THROUGHPUT = [Units.QUERY_PER_S]
UNITS_POWER = [Units.WATTS]

###############################################################################
# Stack details
###############################################################################

class ComponentMetadata(BaseModel):
    """Component metadata."""

    model_config = MODEL_CONFIG.copy()

    kind: str
    """The type of component."""
    schema_version: str = "0.0.1"
    """Schema version for the component."""
    label: str
    """Unique name for this particular component."""
    cfg_id: str
    """Configuration ID, a hash of this component's configuration."""
    description: str | None = None
    """Description of this component."""


class ComponentNative(BaseModel):
    """Component configuration in native format."""

    model_config = MODEL_CONFIG.copy()

    args: dict[str, Any] | None = None
    """Command line arguments."""
    envars: dict[str, Any] | None = None
    """Environment variables."""
    config: Any | None = None
    """Configuration file details."""


class Component(BaseModel):
    """Component details."""

    model_config = MODEL_CONFIG.copy()

    metadata: ComponentMetadata
    """Component metadata."""
    standardized: ComponentStandardizedBase | ComponentStandardizedTolerantBase
    """Component configuration details in standardized format."""
    Native: ComponentNative
    """Component configuration in native format."""

###############################################################################
# Experimental workload
###############################################################################

class LoadMetadata(BaseModel):
    """Workload metadata."""

    model_config = MODEL_CONFIG.copy()

    schema_version: str = "0.0.1"
    """Version of workload description schema."""
    cfg_id: str | None = None
    """Configuration ID, a hash of the workload configuration."""
    description: str = None
    """Descriptin of workload."""


class SLDistribution(StrEnum):
    """Sequence length distribution type.

    Attributes
        FIXED: str
            Length is a fixed value.
        GAUSSIAN: str
            Gaussian distribution, with a mean and standard deviation.
        RANDOM: str
            Uniform distribution between a minimum and maximum value.
        UNIFORM: str
            Alias for random distribution.
        OTHER: str
            An otherwise undefined distribution.
    """

    FIXED = auto()
    GAUSSIAN = auto()
    RANDOM = auto()
    UNIFORM = RANDOM
    OTHER = auto()



class SequenceLength(BaseModel):
    """Sequence length."""

    model_config = MODEL_CONFIG.copy()

    distribution: SLDistribution
    """Sequence length distribution type."""
    value: int | float
    """Primary value."""
    std_dev: float | None = None
    """Standard deviation (if Gaussian)."""
    min: int | None = None
    """Minimum value."""
    max: int | None = None
    """Maximum value."""


class LoadPrefix(BaseModel):
    """Input sequence prefix details."""

    model_config = MODEL_CONFIG.copy()

    prefix_len: SequenceLength
    """Length of common prefix."""
    num_groups: int
    """Number of groups of "users" that share common prefixes."""
    num_users_per_group: int
    """Number of users per group."""
    num_prefixes: int
    """Number of common prefixes within a group."""


class MultiTurn(BaseModel):
    """Multi-turn request configuration."""

    model_config = MODEL_CONFIG.copy()

    enabled: bool = True
    """Multi-turn requests are enabled."""
    max_turns: SequenceLength | None = None
    """Maximum number of requests per session."""


class LoadStandardized(BaseModel):
    """Workload generator configuration details in standardized format."""

    model_config = MODEL_CONFIG.copy()

    tool: str
    """Particular tool used for this component."""
    tool_version: str
    """Version of tool."""
    stage: int = 1
    """Workload stage number (if multi-stage)."""
    input_seq_len: SequenceLength
    """Input sequence length."""
    output_seq_len: SequenceLength | None = None
    """Output sequence length (if enforced)."""
    prefix: LoadPrefix | None = None
    """Input sequence prefix details."""
    multi_turn: MultiTurn | None = None
    """Multi-turn request configuration."""
    rate_qps: float | None = None
    """Request rate, in queries per second."""
    concurrency: int | None = None
    """Request concurrency."""


class LoadNative(BaseModel):
    """Workload generator configuration in native format."""

    model_config = MODEL_CONFIG.copy()

    args: dict[str, Any] | None = None
    """Command line arguments."""
    envars: dict[str, Any] | None = None
    """Environment variables."""
    config: Any | None = None
    """Configuration file details."""

#------------------------------------------------------------------------------
# Root for load
#------------------------------------------------------------------------------

class Load(BaseModel):
    """Experimental workload details."""

    model_config = MODEL_CONFIG.copy()

    metadata: LoadMetadata
    """Workload metadata."""
    standardized: LoadStandardized
    """Workload generator configuration details in standardized format."""
    Native: LoadNative
    """Workload generator configuration in native format."""

###############################################################################
# Request-level metrics
###############################################################################

#------------------------------------------------------------------------------
# Aggregate request performance
#------------------------------------------------------------------------------

class Statistics(BaseModel):
    """Statistical information about a property."""

    units: Units
    mean: float
    mode: float | int | None = None
    stddev: float | None = None
    min: float | int | None = None
    p0p1: float | int | None = None
    p1: float | int | None = None
    p5: float | int | None = None
    p10: float | int | None = None
    p25: float | int | None = None
    p50: float | int | None = None # This is the same as median
    p75: float | int | None = None
    p90: float | int | None = None
    p95: float | int | None = None
    p99: float | int | None = None
    p99p9: float | int | None = None
    max: float | int | None = None


class AggregateRequests(BaseModel):
    """Request statistics."""

    model_config = MODEL_CONFIG.copy()

    total: int
    """Total number of requests sent."""
    failures: int | None = None
    """Number of requests which responded with an error."""
    incomplete: int | None = None
    """Number of requests which were not completed."""
    input_length: Statistics | None = None
    """Input sequence length."""
    output_length: Statistics | None = None
    """Output sequence length."""

    @model_validator(mode='after')
    def check_units(self):
        if self.input_length and self.input_length.units not in UNITS_QUANTITY:
            raise ValueError(
                f'Invalid units "{self.input_length.units}", must be one of:'
                f' {" ".join(UNITS_QUANTITY)}'
            )
        if self.output_length and self.output_length.units not in UNITS_QUANTITY:
            raise ValueError(
                f'Invalid units "{self.output_length.units}", must be one of:'
                f' {" ".join(UNITS_QUANTITY)}'
            )
        return self


class AggregateLatency(BaseModel):
    """Aggregate response latency performance metrics."""

    model_config = MODEL_CONFIG.copy()

    time_to_first_token: Statistics | None = None
    """Time to generate the first token (TTFT)."""
    normalized_time_per_output_token: Statistics | None = None
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
    time_per_output_token: Statistics | None = None
    """Time to generate an output token, excluding first (TPOT, may differ from ITL depending on tool)."""
    inter_token_latency: Statistics | None = None
    """Latency between generated tokens, excluding first (ITL, may differ from TPOT depending on tool)."""
    request_latency: Statistics | None = None
    """End-to-end request latency."""

    @model_validator(mode='after')
    def check_units(self):
        if self.time_to_first_token and self.time_to_first_token.units not in UNITS_TIME:
            raise ValueError(
                f'Invalid units "{self.time_to_first_token.units}", must be'
                f' one of: {" ".join(UNITS_TIME)}')
        if self.normalized_time_per_output_token and self.normalized_time_per_output_token.units not in UNITS_GEN_LATENCY:
            raise ValueError(
                f'Invalid units "{self.normalized_time_per_output_token.units}"'
                f', must be one of: {" ".join(UNITS_GEN_LATENCY)}')
        if self.time_per_output_token and self.time_per_output_token.units not in UNITS_GEN_LATENCY:
            raise ValueError(
                f'Invalid units "{self.time_per_output_token.units}", must be'
                f' one of: {" ".join(UNITS_GEN_LATENCY)}')
        if self.inter_token_latency and self.inter_token_latency.units not in UNITS_GEN_LATENCY:
            raise ValueError(
                f'Invalid units "{self.inter_token_latency.units}", must be'
                f' one of: {" ".join(UNITS_GEN_LATENCY)}')
        if self.request_latency and self.request_latency.units not in UNITS_TIME:
            raise ValueError(
                f'Invalid units "{self.request_latency.units}", must be'
                f' one of: {" ".join(UNITS_TIME)}')
        return self


class AggregateThroughput(BaseModel):
    """Aggregate response throughput performance metrics."""

    model_config = MODEL_CONFIG.copy()

    input_token_rate: Statistics | None = None
    """Input token rate."""
    output_token_rate: Statistics | None = None
    """Output token rate."""
    total_token_rate: Statistics | None = None
    """Total token rate (input + output)."""
    requests_rate: Statistics | None = None
    """Request (query) processing rate."""

    @model_validator(mode='after')
    def check_units(self):
        if self.input_token_rate and self.input_token_rate.units not in UNITS_GEN_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.input_token_rate.units}", must be'
                f' one of: {" ".join(UNITS_GEN_THROUGHPUT)}')
        if self.output_token_rate and self.output_token_rate.units not in UNITS_GEN_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.output_token_rate.units}"'
                f', must be one of: {" ".join(UNITS_GEN_THROUGHPUT)}')
        if self.total_token_rate and self.total_token_rate.units not in UNITS_GEN_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.total_token_rate.units}", must be'
                f' one of: {" ".join(UNITS_GEN_THROUGHPUT)}')
        if self.requests_rate and self.requests_rate.units not in UNITS_REQUEST_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.requests_rate.units}", must be'
                f' one of: {" ".join(UNITS_REQUEST_THROUGHPUT)}')
        return self


class AggregateRequestPerformance(BaseModel):
    """Aggregate performance metrics."""

    model_config = MODEL_CONFIG.copy()

    requests: AggregateRequests | None = None
    """Aggregate request details."""
    latency: AggregateLatency | None = None
    """Aggregate response latency performance metrics."""
    throughput: AggregateThroughput | None = None
    """Aggregate response throughput performance metrics."""

#------------------------------------------------------------------------------
# Time series request performance
#------------------------------------------------------------------------------

class TimeSeriesPoint(BaseModel):
    """Time series data point."""

    model_config = MODEL_CONFIG.copy()

    ts: str
    """ISO‑8601 timestamp."""
    value: str | float | int | bool | None = None
    """Value for datapoint."""
    mean: float | None = None
    mode: float | int | None = None
    stddev: float | None = None
    min: float | int | None = None
    p0p1: float | int | None = None
    p1: float | int | None = None
    p5: float | int | None = None
    p10: float | int | None = None
    p25: float | int | None = None
    p50: float | int | None = None # This is the same as median
    p75: float | int | None = None
    p90: float | int | None = None
    p95: float | int | None = None
    p99: float | int | None = None
    p99p9: float | int | None = None
    max: float | int | None = None


class TimeSeriesData(BaseModel):
    """Time series data."""

    model_config = MODEL_CONFIG.copy()

    units: Units
    """Units for time series."""
    series: list[TimeSeriesPoint]
    """Time series data points."""


class TimeSeriesLatency(BaseModel):
    """Time series latency metrics."""

    model_config = MODEL_CONFIG.copy()

    time_to_first_token: TimeSeriesData | None = None
    """Time to generate the first token (TTFT)."""
    normalized_time_per_output_token: TimeSeriesData | None = None
    """Typical time to generate an output token, including first (NTPOT)."""
    time_per_output_token: TimeSeriesData | None = None
    """Time to generate an output token, excluding first (TPOT, may differ from ITL depending on tool)."""
    inter_token_latency: TimeSeriesData | None = None
    """Latency between generated tokens, excluding first (ITL, may differ from TPOT depending on tool)."""
    request_latency: TimeSeriesData | None = None
    """End-to-end request latency."""

    @model_validator(mode='after')
    def check_units(self):
        if self.time_to_first_token and self.time_to_first_token.units not in UNITS_TIME:
            raise ValueError(
                f'Invalid units "{self.time_to_first_token.units}", must be'
                f' one of: {" ".join(UNITS_TIME)}')
        if self.normalized_time_per_output_token and self.normalized_time_per_output_token.units not in UNITS_GEN_LATENCY:
            raise ValueError(
                f'Invalid units "{self.normalized_time_per_output_token.units}"'
                f', must be one of: {" ".join(UNITS_GEN_LATENCY)}')
        if self.time_per_output_token and self.time_per_output_token.units not in UNITS_GEN_LATENCY:
            raise ValueError(
                f'Invalid units "{self.time_per_output_token.units}", must be'
                f' one of: {" ".join(UNITS_GEN_LATENCY)}')
        if self.inter_token_latency and self.inter_token_latency.units not in UNITS_GEN_LATENCY:
            raise ValueError(
                f'Invalid units "{self.inter_token_latency.units}", must be'
                f' one of: {" ".join(UNITS_GEN_LATENCY)}')
        if self.request_latency and self.request_latency.units not in UNITS_TIME:
            raise ValueError(
                f'Invalid units "{self.request_latency.units}", must be'
                f' one of: {" ".join(UNITS_TIME)}')
        return self


class TimeSeriesThroughput(BaseModel):
    """Time series throughput metrics."""

    model_config = MODEL_CONFIG.copy()

    units: Units = Units.TOKEN_PER_S

    input_token_rate: TimeSeriesData | None = None
    """Input token rate."""
    output_token_rate: TimeSeriesData | None = None
    """Output token rate."""
    total_token_rate: TimeSeriesData | None = None
    """Total token rate (input + output)."""
    requests_rate: TimeSeriesData | None = None
    """Request (query) processing rate."""

    @model_validator(mode='after')
    def check_units(self):
        if self.input_token_rate and self.input_token_rate.units not in UNITS_GEN_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.input_token_rate.units}", must be'
                f' one of: {" ".join(UNITS_GEN_THROUGHPUT)}')
        if self.output_token_rate and self.output_token_rate.units not in UNITS_GEN_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.output_token_rate.units}"'
                f', must be one of: {" ".join(UNITS_GEN_THROUGHPUT)}')
        if self.total_token_rate and self.total_token_rate.units not in UNITS_GEN_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.total_token_rate.units}", must be'
                f' one of: {" ".join(UNITS_GEN_THROUGHPUT)}')
        if self.requests_rate and self.requests_rate.units not in UNITS_REQUEST_THROUGHPUT:
            raise ValueError(
                f'Invalid units "{self.requests_rate.units}", must be'
                f' one of: {" ".join(UNITS_REQUEST_THROUGHPUT)}')
        return self


class TimeSeriesRequestPerformance(BaseModel):
    """Time series performance metrics."""

    model_config = MODEL_CONFIG.copy()

    latency: TimeSeriesLatency | None = None
    """Time series latency metrics."""
    throughput: TimeSeriesThroughput | None = None
    """Time series throughput metrics."""

#------------------------------------------------------------------------------
# Root for request performance
#------------------------------------------------------------------------------

class RequestPerformance(BaseModel):
    """Request-level performance metrics."""

    model_config = MODEL_CONFIG.copy()

    aggregate: AggregateRequestPerformance | None = None
    """Aggregate performance metrics."""
    time_series: TimeSeriesRequestPerformance | None = None
    """Time series metrics."""

###############################################################################
# Observability metrics
###############################################################################


#------------------------------------------------------------------------------
# Root for observability
#------------------------------------------------------------------------------

class Observability(BaseModel):
    """Observability metrics."""

    model_config = MODEL_CONFIG.copy()

###############################################################################
# Benchmark Report top-level classes
###############################################################################

class RunTime(BaseModel):
    """Time details of experiment."""

    model_config = MODEL_CONFIG.copy()

    start: datetime.datetime | None = None
    """ISO‑8601 timestamp for experiment start."""
    end: datetime.datetime | None = None
    """ISO‑8601 timestamp for experiment end."""
    duration: str | None = None
    """ISO‑8601 duration for experiment."""


class Run(BaseModel):
    """Benchmark run details."""

    model_config = MODEL_CONFIG.copy()

    uid: str | None = None
    """Unique ID for this specific benchmark report."""
    eid: str | None = None
    """Experiment ID, common across benchmark reports from a particular experiment."""
    cid: str | None = None
    """Cluster ID, unique to a particular cluster."""
    time: RunTime | None = None
    """Time details of experiment."""
    user: str | None = None
    """Username that executed experiment."""


class Scenario(BaseModel):
    """Benchmark run details."""

    model_config = MODEL_CONFIG.copy()

    stack: list[Component] | None = None
    """List of components used to build the stack."""
    load: Load | None = None
    """Experimental workload details."""


class Results(BaseModel):
    """Benchmark run details."""

    model_config = MODEL_CONFIG.copy()

    request_performance: RequestPerformance | None = None
    """Request-level performance metrics."""

    observability: Observability | None = None
    """Observability metrics."""

    profiling: Any | None = None
    """Profiling results."""

#------------------------------------------------------------------------------
# Root class for benchmark report
#------------------------------------------------------------------------------

class BenchmarkReportV02(BenchmarkReport):
    """Base class for a benchmark report."""

    model_config = MODEL_CONFIG.copy()
    model_config["title"] = "Benchmark Report v0.2"

    version: str = VERSION
    """Version of the schema."""
    run: Run
    """Benchmark run details."""
    scenario: Scenario | None = None
    """Stack configuration and workload details of experiment"""
    results: Results
    """Experiment results."""
