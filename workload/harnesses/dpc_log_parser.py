"""Parse DPC (dual-pods-controller) klog output to extract per-request timing.

The DPC emits structured klog lines at V(2) with httpCallStartTime/k8sCallStartTime
fields (added in llm-d-fast-model-actuation PR #522). This module extracts those
timestamps and correlates them by requesterName to produce per-request timing records.
"""

import glob
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# klog structured field patterns (key="value" pairs)
_FIELD_RE = re.compile(r'(\w+)="([^"]*)"')

# Log messages we care about
_RELAY_READINESS_MSG = "Successfully relayed the readiness"
_WAKE_MSG = "Woke inference server"
_CREATE_INSTANCE_MSG = "Created vLLM instance"
_CREATE_LAUNCHER_POD_MSG = "Created launcher-based server-providing pod"

_DPC_INDICATOR_MSGS = (
    _RELAY_READINESS_MSG,
    _WAKE_MSG,
    _CREATE_INSTANCE_MSG,
    _CREATE_LAUNCHER_POD_MSG,
)


def _parse_rfc3339_nano(s: str) -> Optional[float]:
    """Parse RFC3339Nano timestamp string to Unix epoch float (seconds)."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


@dataclass
class DPCTimingRecord:
    """Timing anchors for a single requester pod, extracted from DPC logs."""

    requester_name: str
    relay_readiness_time: Optional[float] = None
    wake_start_time: Optional[float] = None
    instance_create_start_time: Optional[float] = None
    launcher_create_start_time: Optional[float] = None

    def t_hot(self) -> Optional[float]:
        """T_hot: relay_readiness - wake_start (seconds)."""
        if self.relay_readiness_time is not None and self.wake_start_time is not None:
            return self.relay_readiness_time - self.wake_start_time
        return None

    def t_instance_create(self) -> Optional[float]:
        """T_instance_create: relay_readiness - instance_create_start (seconds)."""
        if (
            self.relay_readiness_time is not None
            and self.instance_create_start_time is not None
        ):
            return self.relay_readiness_time - self.instance_create_start_time
        return None

    def t_cold_launcher(self) -> Optional[float]:
        """T_cold_launcher: relay_readiness - launcher_create_start (seconds)."""
        if (
            self.relay_readiness_time is not None
            and self.launcher_create_start_time is not None
        ):
            return self.relay_readiness_time - self.launcher_create_start_time
        return None


def parse_dpc_log(lines: Iterable[str]) -> dict[str, DPCTimingRecord]:
    """Parse DPC log lines and return timing records keyed by requester pod name.

    Accepts any iterable of strings (list, file object, generator) to support
    both in-memory and streaming use without loading the entire file at once.

    Lines without a requesterName field or without a recognized timing message
    are skipped.

    Args:
        lines: Iterable of log line strings.

    Returns:
        Dict mapping requester pod name to its DPCTimingRecord.
    """
    records: dict[str, DPCTimingRecord] = {}

    for line in lines:
        if not any(msg in line for msg in _DPC_INDICATOR_MSGS):
            continue

        fields = dict(_FIELD_RE.findall(line))

        requester_name = fields.get("requesterName")
        if not requester_name:
            continue

        if requester_name not in records:
            records[requester_name] = DPCTimingRecord(requester_name=requester_name)
        rec = records[requester_name]

        if _RELAY_READINESS_MSG in line and fields.get("readiness") == "ready":
            ts = _parse_rfc3339_nano(fields.get("httpCallStartTime", ""))
            if ts is not None:
                rec.relay_readiness_time = ts

        elif _WAKE_MSG in line:
            ts = _parse_rfc3339_nano(fields.get("httpCallStartTime", ""))
            if ts is not None:
                rec.wake_start_time = ts

        elif _CREATE_INSTANCE_MSG in line:
            ts = _parse_rfc3339_nano(fields.get("httpCallStartTime", ""))
            if ts is not None:
                rec.instance_create_start_time = ts

        elif _CREATE_LAUNCHER_POD_MSG in line:
            ts = _parse_rfc3339_nano(fields.get("k8sCallStartTime", ""))
            if ts is not None:
                rec.launcher_create_start_time = ts

    return records


def _is_dpc_log_file(filepath: str) -> bool:
    """Check if a file is a DPC log by streaming for any indicator message.

    Streams line-by-line and returns as soon as an indicator message is found,
    so it is cheap for real DPC logs (the first indicator may appear well past
    the start, after controller startup noise) and only reads to EOF for a
    non-DPC log.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if any(msg in line for msg in _DPC_INDICATOR_MSGS):
                    return True
    except OSError:
        return False
    return False


def parse_dpc_log_file(requests_dir: str) -> dict[str, DPCTimingRecord]:
    """Find and parse DPC controller log file(s) in the requests directory.

    Streams the file line-by-line to handle arbitrarily large logs without
    loading the entire content into memory.

    The log file is written by write_controller_log() with the naming pattern:
    <pod-name>--<container-name>.log. We identify DPC logs by streaming for any
    timing-relevant message (handles the case where requesters crashed before
    relay and the log only contains start events, and the case where the first
    indicator message appears far into a large log after controller startup
    noise).

    Args:
        requests_dir: Directory where controller logs were saved.

    Returns:
        Dict mapping requester pod name to its DPCTimingRecord.
        Returns empty dict if directory doesn't exist or no DPC log found.
    """
    if not os.path.isdir(requests_dir):
        logger.warning("DPC log directory does not exist: %s", requests_dir)
        return {}

    log_files = glob.glob(os.path.join(requests_dir, "*.log"))
    if not log_files:
        logger.debug("No .log files found in %s", requests_dir)
        return {}

    all_records: dict[str, DPCTimingRecord] = {}
    for log_file in log_files:
        if not _is_dpc_log_file(log_file):
            continue

        logger.info("Parsing DPC log for timing: %s", log_file)
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                records = parse_dpc_log(f)
            all_records.update(records)
        except OSError as e:
            logger.warning("Failed to read log file %s: %s", log_file, e)

    return all_records
