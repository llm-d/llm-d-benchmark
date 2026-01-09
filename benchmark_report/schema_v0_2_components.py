"""
Standardized component classes for v0.2 benchmark reports.
"""

from enum import StrEnum, auto
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Default model_config to apply to Pydantic classes
MODEL_CONFIG = ConfigDict(
    extra="forbid", # Do not allow fields that are not part of this schema
    use_attribute_docstrings=True, # Use docstrings for JSON schema
    populate_by_name=False, # Must use alias name, not internal field name
    validate_assignment=True, # Validate field assignment after init
)

###############################################################################
# Base class for standardized section of a component
###############################################################################

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

###############################################################################
# Generic component, kind: generic
#
# Use for any components that do not have a formal schema defined for the
# standardized section.
###############################################################################

class Generic(BaseModel):
    """Component configuration for a generic component.

    This class  allows for extra attributes to be added without validation.
    Use this for development of new component classes, or when a class for your
    component does not exist but you don't want to write your own class.
    """

    model_config = MODEL_CONFIG.copy()
    model_config["extra"] = "allow" # Here we allow for extra unvalidated fields

    kind: Literal["generic"] = Field(
        exclude=True,
        json_schema_extra={"exclude": True},
        description=(
            "Do not populate this field, this is for internal validation and"
            " will be copied over from the metadata section."
        )
    )
    tool: str
    """Particular tool used for this component."""
    tool_version: str
    """Version of tool."""

###############################################################################
# Inference engine, kind: inference_engine
###############################################################################

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

    kind: Literal["inference_engine"] = Field(
        exclude=True,
        json_schema_extra={"exclude": True},
        description=(
            "Do not populate this field, this is for internal validation and"
            " will be copied over from the metadata section."
        )
    )
    role: HostType
    """Type of model serving host."""
    model: InferenceEngineModel
    """Hosted model details."""
    accelerator: InferenceEngineAccelerator
    """Accelerator hardware details."""


# All supported component classes
COMPONENTS = Generic | InferenceEngine

