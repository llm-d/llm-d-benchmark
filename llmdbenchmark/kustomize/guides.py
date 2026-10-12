"""Helpers for identifying llm-d guides by their kustomize ``guideName``."""

FMA_GUIDE_NAME = "fast-model-actuation"


def guide_basename(guide_name: str) -> str:
    """Return the last path segment of a ``guideName``.

    Guides may be nested under ``guides/`` (``multimodal-serving/aggregation``,
    ``fast-model-actuation/fast-model-actuation-keda``). The basename is the
    guide's ``GUIDE_NAME``: its Helm release, ``llm-d.ai/guide`` label and
    ``<name>-epp`` Service.
    """
    return guide_name.rstrip("/").split("/")[-1]


def is_fma_guide(guide_name: str) -> bool:
    """True for every Fast Model Actuation guide variant.

    Matches the base guide (``fast-model-actuation``, or the legacy
    ``fast-model-actuation-base``) and derivatives such as
    ``fast-model-actuation-keda``, whether top-level or nested under
    ``fast-model-actuation/``.
    """
    name = guide_basename(guide_name or "")
    return name == FMA_GUIDE_NAME or name.startswith(FMA_GUIDE_NAME + "-")
