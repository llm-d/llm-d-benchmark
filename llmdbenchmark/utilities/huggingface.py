"""HuggingFace Hub helpers for gated-model detection and token access verification."""

from dataclasses import dataclass
from enum import Enum

from huggingface_hub import (
    model_info as hf_model_info,
    HfApi,
)
from huggingface_hub.utils import (
    GatedRepoError,
    RepositoryNotFoundError,
    HfHubHTTPError,
)


class GatedStatus(Enum):
    NOT_GATED = "not_gated"
    GATED = "gated"
    ERROR = "error"


class AccessStatus(Enum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    ERROR = "error"


@dataclass
class ModelAccessResult:
    """Combined result of gated and access checks for a single model."""

    model_id: str
    gated: GatedStatus
    access: AccessStatus | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True if the model is accessible."""
        if self.gated == GatedStatus.NOT_GATED:
            return True
        if self.gated == GatedStatus.GATED:
            return self.access == AccessStatus.AUTHORIZED
        return True


def is_model_gated(model_id: str) -> GatedStatus:
    """Check whether a HuggingFace model requires access approval."""
    try:
        info = hf_model_info(model_id)
        if info.gated is False or info.gated == "false":
            return GatedStatus.NOT_GATED
        return GatedStatus.GATED
    except RepositoryNotFoundError:
        return GatedStatus.ERROR
    except (HfHubHTTPError, Exception):
        return GatedStatus.ERROR


def user_has_model_access(model_id: str, hf_token: str) -> AccessStatus:
    """Verify that a HuggingFace token has access to a gated model."""
    try:
        hf_model_info(model_id, token=hf_token)
        return AccessStatus.AUTHORIZED
    except GatedRepoError:
        return AccessStatus.UNAUTHORIZED
    except RepositoryNotFoundError:
        return AccessStatus.UNAUTHORIZED
    except HfHubHTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            return AccessStatus.UNAUTHORIZED
        return AccessStatus.ERROR
    except Exception:
        return AccessStatus.ERROR


def check_model_access(model_id: str, hf_token: str | None = None) -> ModelAccessResult:
    """Check whether a model is gated and verify token access if so."""
    gated = is_model_gated(model_id)

    if gated == GatedStatus.NOT_GATED:
        return ModelAccessResult(
            model_id=model_id,
            gated=gated,
            detail=(
                f'Model "{model_id}" is not gated — '
                f"access is authorized by default"
            ),
        )

    if gated == GatedStatus.ERROR:
        return ModelAccessResult(
            model_id=model_id,
            gated=gated,
            detail=(
                f'Could not determine gating status for "{model_id}" '
                f"(HuggingFace API request failed) — proceeding anyway"
            ),
        )

    if not hf_token or hf_token in ("REPLACE_TOKEN", ""):
        return ModelAccessResult(
            model_id=model_id,
            gated=gated,
            access=AccessStatus.UNAUTHORIZED,
            detail=(
                f'Model "{model_id}" is gated but no HuggingFace token '
                f"was provided. Either export HF_TOKEN in your environment "
                f"or set huggingface.token in your scenario YAML."
            ),
        )

    access = user_has_model_access(model_id, hf_token)

    if access == AccessStatus.AUTHORIZED:
        return ModelAccessResult(
            model_id=model_id,
            gated=gated,
            access=access,
            detail=(
                f'Verified access to gated model "{model_id}" '
                f"is authorized"
            ),
        )

    if access == AccessStatus.UNAUTHORIZED:
        return ModelAccessResult(
            model_id=model_id,
            gated=gated,
            access=access,
            detail=(
                f'Unauthorized access to gated model "{model_id}". '
                f"Your HuggingFace token does not have access to this "
                f"model. Visit https://huggingface.co/{model_id} to "
                f"request access."
            ),
        )

    return ModelAccessResult(
        model_id=model_id,
        gated=gated,
        access=access,
        detail=(
            f"Could not verify token access to gated model "
            f'"{model_id}" (request failed) — proceeding anyway'
        ),
    )
