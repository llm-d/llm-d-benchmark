"""Per-scenario validator registry.

Every scenario with a dedicated validator gets scenario-specific config
validation (step 2).  When a stack name is not found in the registry,
``get_validator()`` falls back to ``BaseSmoketest`` which still runs
generic health checks and inference tests (steps 0 and 1) -- it just
skips scenario-specific config validation.
"""

# Guides (well-lit paths)
from llmdbenchmark.smoketests.validators.pd_disaggregation import (
    PdDisaggregationValidator,
)
from llmdbenchmark.smoketests.validators.precise_prefix_cache_aware import (
    PrecisePrefixCacheAwareValidator,
)
from llmdbenchmark.smoketests.validators.inference_scheduling import (
    InferenceSchedulingValidator,
)
from llmdbenchmark.smoketests.validators.tiered_prefix_cache import (
    TieredPrefixCacheValidator,
)
from llmdbenchmark.smoketests.validators.wide_ep_lws import (
    WideEpLwsValidator,
)
from llmdbenchmark.smoketests.validators.simulated_accelerators import (
    SimulatedAcceleratorsValidator,
)
from llmdbenchmark.smoketests.validators.wva import WvaValidator

# Examples
from llmdbenchmark.smoketests.validators.cpu import CpuValidator
from llmdbenchmark.smoketests.validators.gpu import GpuValidator
from llmdbenchmark.smoketests.validators.spyre import SpyreValidator


VALIDATORS: dict[str, type] = {
    # Guides (well-lit paths)
    "pd-disaggregation": PdDisaggregationValidator,
    "precise-prefix-cache-aware": PrecisePrefixCacheAwareValidator,
    "inference-scheduling": InferenceSchedulingValidator,
    # inference-scheduling-wva reuses the inference-scheduling validator;
    # the WvaSmoketestMixin auto-activates its extra checks when the
    # stack's config has wva.enabled: true.
    "inference-scheduling-wva": InferenceSchedulingValidator,
    "tiered-prefix-cache": TieredPrefixCacheValidator,
    "wide-ep-lws": WideEpLwsValidator,
    "simulated-accelerators": SimulatedAcceleratorsValidator,
    "wva": WvaValidator,
    # Examples
    "cpu-example-ms": CpuValidator,
    "gpu-example": GpuValidator,
    "spyre-example": SpyreValidator,
}
