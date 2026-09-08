"""Session-lifecycle metric paths and dict traversal, shared by driver and pod.

Split out of ``cross_treatment`` so the in-pod plot scripts can import the same
table: that module pulls in the driver-only archive reader, which the image does
not ship.
"""

from __future__ import annotations

SESSION_METRICS_OF_INTEREST = [
    ("results.session_performance.sessions.session_rate.mean", "session_rate_qps"),
    (
        "results.session_performance.sessions.session_duration.mean",
        "session_duration_mean_s",
    ),
    (
        "results.session_performance.sessions.session_duration.p50",
        "session_duration_p50_s",
    ),
    (
        "results.session_performance.sessions.session_duration.p99",
        "session_duration_p99_s",
    ),
    (
        "results.session_performance.sessions.events_per_session.mean",
        "events_per_session_mean",
    ),
    (
        "results.session_performance.sessions.events_cancelled_per_session.mean",
        "events_cancelled_per_session_mean",
    ),
    (
        "results.session_performance.sessions.input_tokens_per_session.mean",
        "input_tokens_per_session_mean",
    ),
    (
        "results.session_performance.sessions.output_tokens_per_session.mean",
        "output_tokens_per_session_mean",
    ),
    ("results.session_performance.sessions.total", "total_sessions"),
    ("results.session_performance.sessions.failed", "failed_sessions"),
]


def deep_get(d: dict, dotted_key: str, default=None):
    """Traverse nested dict by dotted key path."""
    keys = dotted_key.split(".")
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is default:
            return default
    return d
