"""Shared SCC elevation detection, used by standup steps 01 and 08.

One place decides what counts as an elevated security context and which
service account runs the pods, so the early warning and the late one
cannot drift apart.
"""

_SERVICE_SECTIONS = (
    "standalone",
    "vllmCommon",
    "decode",
    "prefill",
    "vllmModelserviceDecode",
    "vllmModelservicePrefill",
)


def scenario_requests_elevation(config: dict) -> bool:
    """True when a serving section asks for root or root group."""
    for name in _SERVICE_SECTIONS:
        section = config.get(name) or {}
        security = section.get("securityContext") or {}
        if security.get("runAsUser") == 0 or security.get("runAsGroup") == 0:
            return True
        extra = section.get("extraContainerConfig") or {}
        extra_security = extra.get("securityContext") or {}
        if (
            extra_security.get("runAsUser") == 0
            or extra_security.get("runAsGroup") == 0
        ):
            return True
    return False


def elevated_service_account(config: dict) -> str:
    """The service account the chart runs the serving pods as."""
    return config.get("serviceAccountOverride", "") or config.get("model_id_label", "")
