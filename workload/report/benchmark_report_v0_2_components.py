"""
Standardized component classes for v0.2 benchmark reports.
"""

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Default model_config to apply to Pydantic classes
MODEL_CONFIG = ConfigDict(
    extra="forbid", # Do not allow fields that are not part of this schema
    use_attribute_docstrings=True, # Use docstrings for JSON schema
    populate_by_name=False, # Must use alias name, not internal field name
    validate_assignment=True, # Validate field assignment after init
)

class ComponentStandardizedBase(BaseModel):
    """Component configuration details in standardized format.

    This class is a base class that should be inherited by the class that
    defines the actual native format for a particular component type. Only the
    attributes defined here will be common across all component types.
    """

    model_config = MODEL_CONFIG.copy()

    tool: str
    """Particular tool used for this component."""
    tool_version: str
    """Version of tool."""


class ComponentStandardizedTolerantBase(BaseModel):
    """Component configuration details in loosely defined standardized format.

    This class is a base class that can be used on its own, or inherited by the
    a class defining a native format for a particular component type.

    This base allows for extra attributes to be added without validation (most
    benchmark report classes forbid this). Use this base for development of
    component classes, or when a class for your component does not exist but
    you don't want to write your own class.
    """

    model_config = MODEL_CONFIG.copy()
    model_config["extra"] = "allow"

    tool: str
    """Particular tool used for this component."""
    tool_version: str
    """Version of tool."""
    tolerant_schema: bool = Field(
        True,
        description=(
            "This field is to distinguish between a tolerant "
            '"standardized" component schema, and the usual strict schema which'
            "does not allow fields to be added that are not part of the defined"
            "schema. The value of this field does not change behavior, its"
            "existence alone indicates a tolerant schema."
        )
    )

    @model_validator(mode="after")
    def enforce_tolerant_true(self):
        """To avoid confusion, enforce tolerant_schema=True."""
        if not self.tolerant_schema:
            raise ValueError(
                'A tolerant "standardized" schema necessitates tolerant_schema=True'
            )
        return self



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


class InferenceEngineModel(BaseModel):
    """Hosted model details."""

    model_config = MODEL_CONFIG.copy()

    name: str
    """Model name."""


class InferenceEngineParallelism(BaseModel):
    """Parallelism details."""

    model_config = MODEL_CONFIG.copy()

    dp: int = Field(1, ge=1, description="Data parallelism.")
    ep: int = Field(1, ge=1, description="Expert parallelism.")
    pp: int = Field(1, ge=1, description="Pipeline parallelism.")
    tp: int = Field(1, ge=1, description="Tensor parallelism.")


class InferenceEngineAccelerator(BaseModel):
    """Accelerator hardware details."""

    model_config = MODEL_CONFIG.copy()

    model: str
    """Hardware model name."""
    count: int = Field(..., ge=1, description="Total utilized accelerator count.")
    parallelism: InferenceEngineParallelism
    """Parallelism utilized."""


class InferenceEngine(ComponentStandardizedBase):
    """Component configuration for an inference engine."""

    role: HostType
    """Type of model serving host."""
    model: InferenceEngineModel
    """Hosted model details."""
    accelerator: InferenceEngineAccelerator
    """Accelerator hardware details."""
