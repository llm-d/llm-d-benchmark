#!/usr/bin/env python3
# Copyright 2026 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

"""Async/Batch workload harness for llm-d-benchmark.

Enqueues multi-wave asynchronous inference requests with per-request Unix
deadlines into ``llm-d-async`` (via the exact Redis SortedSet Lua wire contract
from ``producer/redis_sortedset_producer.go`` or via ``llm-d-coordinator`` HTTP
``x-llm-d-async-mode: enqueue``), collects completion envelopes, waits for
optional scale-to-zero cooldown / duty-cycle accounting windows, and writes:

- ``results.json``: aggregate + per-wave + per-cohort deadline attainment,
  slack, turnaround, token counts, and accounting window timestamps.
- ``per_request_lifecycle_metrics.json``: per-request lifecycle records.
- ``async_goodput_summary.json``: joined Async Autoscaling Goodput summary once
  ``replica_status.json`` / ``pod_startup_times.json`` are available.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

# Exact atomic Lua script from llm-d-async/producer/redis_sortedset_producer.go:46-57
SUBMIT_REQUEST_LUA = """
local seq = redis.call("INCR", KEYS[1])
redis.call("EXPIREAT", KEYS[1], ARGV[3])
redis.call("DEL", KEYS[2])
redis.call("SET", KEYS[3], ARGV[1], "PX", ARGV[2])
local score = tonumber(ARGV[6]) + math.min(seq, 2097151) / 2097152
redis.call("ZADD", KEYS[4], string.format("%.17g", score), ARGV[4] .. seq .. ARGV[5])
if KEYS[5] then
  redis.call("SET", KEYS[5], ARGV[7], "PX", ARGV[8])
end
return seq
""".strip()

ENQUEUE_SEQ_FIELD_JSON = '"enqueue_seq":'
ENQUEUE_SEQ_PLACEHOLDER_JSON = ENQUEUE_SEQ_FIELD_JSON + "-1"
ENQUEUE_SEQ_GRACE_SECONDS = 3600

SYNTHETIC_WORDS = (
    "async",
    "batch",
    "inference",
    "autoscaling",
    "deadline",
    "goodput",
    "scheduler",
    "throughput",
    "replica",
    "horizon",
    "backlog",
    "cooldown",
    "token",
    "latency",
    "window",
    "capacity",
)


class RedisProtocolError(RuntimeError):
    """Raised when a Redis RESP2 server returns an error or malformed frame."""


class Resp2RedisClient:
    """Zero-dependency pure-Python RESP2 client over TCP sockets.

    Provides ``ping``, ``eval``, ``brpop``, ``zcard``, ``llen``, ``delete`` so
    the harness pod works out-of-the-box even when ``redis-py`` is not installed
    in the container image. Each thread should hold its own client instance.
    """

    def __init__(self, redis_url: str, timeout: float = 10.0) -> None:
        parsed = urllib.parse.urlparse(
            redis_url if "://" in redis_url else f"redis://{redis_url}"
        )
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 6379
        self.username = (
            urllib.parse.unquote(parsed.username) if parsed.username else None
        )
        self.password = (
            urllib.parse.unquote(parsed.password) if parsed.password else None
        )
        db_str = (parsed.path or "").lstrip("/")
        self.db = int(db_str) if db_str.isdigit() else 0
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._reader: Any = None
        self._connect()

    def _connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._reader = sock.makefile("rb")
        if self.password is not None:
            if self.username:
                self.execute("AUTH", self.username, self.password)
            else:
                self.execute("AUTH", self.password)
        if self.db:
            self.execute("SELECT", str(self.db))

    def close(self) -> None:
        try:
            if self._reader is not None:
                self._reader.close()
        except OSError:
            pass
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        self._reader = None
        self._sock = None

    @staticmethod
    def encode_command(*args: str | bytes | int | float) -> bytes:
        parts = [f"*{len(args)}\r\n".encode("ascii")]
        for arg in args:
            if isinstance(arg, bytes):
                raw = arg
            elif isinstance(arg, str):
                raw = arg.encode("utf-8")
            else:
                raw = str(arg).encode("ascii")
            parts.append(f"${len(raw)}\r\n".encode("ascii"))
            parts.append(raw)
            parts.append(b"\r\n")
        return b"".join(parts)

    def _read_line(self) -> bytes:
        assert self._reader is not None
        line = self._reader.readline()
        if not line:
            raise ConnectionError("Redis connection closed unexpectedly")
        if not line.endswith(b"\r\n"):
            raise RedisProtocolError(f"Malformed RESP2 line: {line!r}")
        return line[:-2]

    def _read_reply(self) -> Any:
        assert self._reader is not None
        line = self._read_line()
        if not line:
            raise RedisProtocolError("Empty RESP2 frame")
        prefix = line[:1]
        payload = line[1:]
        if prefix == b"+":
            return payload.decode("utf-8", errors="replace")
        if prefix == b"-":
            raise RedisProtocolError(payload.decode("utf-8", errors="replace"))
        if prefix == b":":
            return int(payload)
        if prefix == b"$":
            length = int(payload)
            if length < 0:
                return None
            data = self._reader.read(length + 2)
            if len(data) < length + 2:
                raise ConnectionError("Truncated RESP2 bulk string")
            return data[:-2].decode("utf-8", errors="replace")
        if prefix == b"*":
            count = int(payload)
            if count < 0:
                return None
            return [self._read_reply() for _ in range(count)]
        raise RedisProtocolError(f"Unknown RESP2 prefix: {prefix!r}")

    def execute(self, *args: str | bytes | int | float) -> Any:
        if self._sock is None:
            self._connect()
        assert self._sock is not None
        self._sock.sendall(self.encode_command(*args))
        return self._read_reply()

    def ping(self) -> bool:
        return self.execute("PING") in ("PONG", True)

    def eval_script(self, script: str, keys: list[str], argv: list[Any]) -> Any:
        return self.execute("EVAL", script, len(keys), *keys, *argv)

    def brpop(
        self, queue_name: str, timeout_seconds: int = 1
    ) -> tuple[str, str] | None:
        res = self.execute("BRPOP", queue_name, int(max(timeout_seconds, 1)))
        if not res or len(res) != 2:
            return None
        return str(res[0]), str(res[1])

    def zcard(self, key: str) -> int:
        return int(self.execute("ZCARD", key) or 0)

    def llen(self, key: str) -> int:
        return int(self.execute("LLEN", key) or 0)

    def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return int(self.execute("DEL", *keys) or 0)


def percentile(values: list[float], pct: float) -> float | None:
    """Linearly interpolated percentile matching process_metrics.py."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = (len(ordered) - 1) * (pct / 100.0)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(ordered[lo])
    frac = idx - lo
    return float(ordered[lo] + frac * (ordered[hi] - ordered[lo]))


def summarize_series(values: list[float], unit: str = "s") -> dict[str, Any]:
    """Build standard summary stats dict for a numeric series."""
    if not values:
        return {
            "count": 0,
            "mean": None,
            "stddev": None,
            "min": None,
            "p10": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
            "unit": unit,
        }
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 4),
        "stddev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        "min": round(min(values), 4),
        "p10": round(percentile(values, 10) or 0.0, 4),
        "p25": round(percentile(values, 25) or 0.0, 4),
        "p50": round(percentile(values, 50) or 0.0, 4),
        "p75": round(percentile(values, 75) or 0.0, 4),
        "p90": round(percentile(values, 90) or 0.0, 4),
        "p95": round(percentile(values, 95) or 0.0, 4),
        "p99": round(percentile(values, 99) or 0.0, 4),
        "max": round(max(values), 4),
        "unit": unit,
    }


def build_synthetic_prompt(prompt_tokens: int, req_id: str) -> str:
    """Generate a deterministic synthetic prompt of approximately prompt_tokens."""
    target_words = max(int(prompt_tokens * 0.75), 4)
    words = [SYNTHETIC_WORDS[i % len(SYNTHETIC_WORDS)] for i in range(target_words)]
    return f"[{req_id}] " + " ".join(words)


def build_openai_payload(
    *,
    api_path: str,
    model_name: str,
    prompt_tokens: int,
    max_tokens: int,
    req_id: str,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Construct an OpenAI completions or chat/completions payload."""
    prompt_text = build_synthetic_prompt(prompt_tokens, req_id)
    if "chat/completions" in api_path:
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt_text}],
            "max_tokens": int(max_tokens),
            "temperature": temperature,
        }
    return {
        "model": model_name,
        "prompt": prompt_text,
        "max_tokens": int(max_tokens),
        "temperature": temperature,
    }


def compute_queue_score(deadline_unix: int, seq: int) -> float:
    """Mirror api.InternalRequest.QueueScore() from llm-d-async/api/internal_api.go."""
    clamped = min(max(int(seq), 0), (1 << 21) - 1)
    return float(deadline_unix) + float(clamped) / float(1 << 21)


def build_sortedset_enqueue_command(
    *,
    req_id: str,
    created_unix: int,
    deadline_unix: int,
    payload: dict[str, Any],
    request_queue_name: str,
    result_queue_name: str,
    endpoint: str = "/v1/completions",
    model_name: str = "",
    metadata: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    request_token: str | None = None,
    now_epoch: float | None = None,
) -> tuple[list[str], list[str], str]:
    """Build exact KEYS and ARGV for SUBMIT_REQUEST_LUA matching RedisSortedSetProducer.

    Returns ``(keys, argv, request_token)``.
    """
    if deadline_unix <= 0:
        raise ValueError("deadline_unix must be positive")
    now_s = float(now_epoch) if now_epoch is not None else time.time()
    active_ttl_ms = max(int(round((deadline_unix - now_s) * 1000.0)), 1)
    seq_expire_at = int(deadline_unix + ENQUEUE_SEQ_GRACE_SECONDS)
    token = request_token or secrets.token_hex(16)

    envelope_obj: dict[str, Any] = {
        "internal": {
            "request_token": token,
            "request_queue_name": request_queue_name,
            "result_queue_name": result_queue_name,
            "enqueue_seq": -1,
        },
        "request_kind": "plain",
        "data": {
            "id": req_id,
            "created": int(created_unix),
            "deadline": int(deadline_unix),
            "payload": payload,
            "metadata": metadata or {},
            "headers": headers or {},
            "endpoint": endpoint,
            "model": model_name,
        },
    }
    serialized = json.dumps(envelope_obj, separators=(",", ":"))
    at = serialized.find(ENQUEUE_SEQ_PLACEHOLDER_JSON)
    if at < 0:
        raise RuntimeError("Marshaled envelope missing enqueue_seq placeholder")

    json_before = serialized[: at + len(ENQUEUE_SEQ_FIELD_JSON)]
    json_after = serialized[at + len(ENQUEUE_SEQ_PLACEHOLDER_JSON) :]

    keys = [
        f"request-seq:{request_queue_name}:{int(deadline_unix)}",
        f"request-cancel:{req_id}",
        f"request-active:{req_id}",
        request_queue_name,
    ]
    argv = [
        token,
        str(active_ttl_ms),
        str(seq_expire_at),
        json_before,
        json_after,
        str(int(deadline_unix)),
    ]
    return keys, argv, token


def parse_result_envelope(
    raw_json: str | dict[str, Any],
    *,
    completed_at: float,
    submitted_at: float,
    deadline_at: float,
    fallback_input_tokens: int = 0,
    fallback_output_tokens: int = 0,
) -> dict[str, Any]:
    """Parse an llm-d-async ResultMessage JSON envelope and evaluate deadline compliance."""
    if isinstance(raw_json, str):
        msg = json.loads(raw_json)
    else:
        msg = dict(raw_json)

    req_id = str(msg.get("id") or "")
    status_code = int(msg.get("status_code") or 0)
    error_code = str(msg.get("error_code") or "")
    error_message = str(msg.get("error_message") or "")
    payload_raw = msg.get("payload")

    input_tokens = 0
    output_tokens = 0
    if isinstance(payload_raw, str) and payload_raw.strip():
        try:
            body = json.loads(payload_raw)
        except json.JSONDecodeError:
            body = None
    elif isinstance(payload_raw, dict):
        body = payload_raw
    else:
        body = None

    if isinstance(body, dict):
        usage = body.get("usage")
        if isinstance(usage, dict):
            input_tokens = int(
                usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            )
            output_tokens = int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )

    is_2xx = (200 <= status_code < 300) and not error_code
    if is_2xx:
        if input_tokens <= 0:
            input_tokens = int(fallback_input_tokens)
        if output_tokens <= 0:
            output_tokens = int(fallback_output_tokens)

    slack_s = float(deadline_at) - float(completed_at)
    on_time = bool(is_2xx and completed_at <= float(deadline_at))
    exceeded_deadline = bool(
        error_code == "DEADLINE_EXCEEDED"
        or status_code == 504
        or (is_2xx and completed_at > float(deadline_at))
    )

    return {
        "id": req_id,
        "status_code": status_code,
        "error_code": error_code,
        "error_message": error_message,
        "submitted_at": round(float(submitted_at), 4),
        "deadline_at": round(float(deadline_at), 4),
        "completed_at": round(float(completed_at), 4),
        "turnaround_s": round(max(float(completed_at) - float(submitted_at), 0.0), 4),
        "slack_s": round(slack_s, 4),
        "succeeded_http": is_2xx,
        "on_time": on_time,
        "exceeded_deadline": exceeded_deadline,
        "input_tokens": input_tokens if is_2xx else 0,
        "output_tokens": output_tokens if is_2xx else 0,
    }


def compute_async_goodput_summary(
    results_data: dict[str, Any],
    replica_status_data: dict[str, Any] | None = None,
    pod_startup_data: dict[str, Any] | None = None,
    min_deadline_attainment: float | None = None,
) -> dict[str, Any]:
    """Join workload results with Allocated GPU-Hours and pod startup stats.

    Emits the canonical ``async_goodput_summary.json`` payload consumed by
    ``score_async_goodput()``, ``native_to_br0_2.py``, and
    ``async_goodput_comparison_table.py``.
    """
    agg = results_data.get("aggregate") or {}
    total_requests = int(agg.get("total_requests") or 0)
    on_time_requests = int(agg.get("on_time_requests") or 0)
    exceeded_deadline = int(agg.get("exceeded_deadline_requests") or 0)
    failed_requests = int(agg.get("failed_requests") or 0)
    on_time_output_tokens = int(agg.get("on_time_output_tokens") or 0)
    on_time_input_tokens = int(agg.get("on_time_input_tokens") or 0)
    attainment = (
        float(on_time_requests) / float(total_requests) if total_requests > 0 else 0.0
    )

    gate_cfg = results_data.get("slo_gate") or {}
    target_attainment = (
        float(min_deadline_attainment)
        if min_deadline_attainment is not None
        else float(gate_cfg.get("min_deadline_attainment", 0.99))
    )

    integral = (replica_status_data or {}).get("resource_integral") or {}
    alloc_gpu_hr = integral.get("allocated_gpu_hours")
    ready_gpu_hr = integral.get("ready_gpu_hours")
    desired_gpu_hr = integral.get("desired_gpu_hours")
    alloc_pod_hr = integral.get("allocated_pod_hours")
    ready_pod_hr = integral.get("ready_pod_hours")
    overhead_frac = integral.get("overhead_gpu_fraction")
    accel_mode = integral.get("accelerator_mode") or "simulated_pod_units"
    window_seconds = integral.get("window_seconds") or results_data.get(
        "accounting_window_seconds"
    )

    req_goodput_gpu_hr: float | None = None
    req_goodput_gpu_min: float | None = None
    tok_goodput_gpu_hr: float | None = None
    if isinstance(alloc_gpu_hr, (int, float)) and alloc_gpu_hr > 0:
        req_goodput_gpu_hr = round(on_time_requests / float(alloc_gpu_hr), 4)
        req_goodput_gpu_min = round(req_goodput_gpu_hr / 60.0, 4)
        tok_goodput_gpu_hr = round(on_time_output_tokens / float(alloc_gpu_hr), 4)

    # Calibrated per-replica throughput R (req / replica-s) while Ready
    calibrated_replica_rps: float | None = None
    if isinstance(ready_pod_hr, (int, float)) and ready_pod_hr > 0:
        calibrated_replica_rps = round(
            on_time_requests / (float(ready_pod_hr) * 3600.0), 6
        )

    # Calibrated cold-start C (p90 pod creation -> Ready seconds)
    calibrated_cold_start_p90_s: float | None = None
    calibrated_cold_start_mean_s: float | None = None
    startup_agg = (pod_startup_data or {}).get("aggregate") or {}
    if isinstance(startup_agg.get("p90"), (int, float)):
        calibrated_cold_start_p90_s = round(float(startup_agg["p90"]), 2)
    if isinstance(startup_agg.get("mean"), (int, float)):
        calibrated_cold_start_mean_s = round(float(startup_agg["mean"]), 2)

    if total_requests <= 0 or alloc_gpu_hr is None or float(alloc_gpu_hr) <= 0:
        verdict = "indeterminate"
    elif attainment >= target_attainment:
        verdict = "pass"
    else:
        verdict = "fail"

    return {
        "schema_version": "1.0",
        "verdict": verdict,
        "min_deadline_attainment": target_attainment,
        "total_requests": total_requests,
        "on_time_requests": on_time_requests,
        "exceeded_deadline_requests": exceeded_deadline,
        "failed_requests": failed_requests,
        "deadline_attainment_rate": round(attainment, 6),
        "on_time_input_tokens": on_time_input_tokens,
        "on_time_output_tokens": on_time_output_tokens,
        "accounting_window_seconds": window_seconds,
        "allocated_gpu_hours": alloc_gpu_hr,
        "ready_gpu_hours": ready_gpu_hr,
        "desired_gpu_hours": desired_gpu_hr,
        "allocated_pod_hours": alloc_pod_hr,
        "ready_pod_hours": ready_pod_hr,
        "overhead_gpu_fraction": overhead_frac,
        "accelerator_mode": accel_mode,
        "request_goodput_per_gpu_hour": req_goodput_gpu_hr,
        "request_goodput_per_gpu_min": req_goodput_gpu_min,
        "output_token_goodput_per_gpu_hour": tok_goodput_gpu_hr,
        "calibrated_replica_rps": calibrated_replica_rps,
        "calibrated_cold_start_p90_s": calibrated_cold_start_p90_s,
        "calibrated_cold_start_mean_s": calibrated_cold_start_mean_s,
        "scale_down_settled": results_data.get("scale_down_settled"),
        "slack_s": agg.get("slack_s"),
        "turnaround_s": agg.get("turnaround_s"),
    }


def _count_scheduled_serving_pods(namespace: str, model_filter: str = "") -> int | None:
    """Query kubectl for scheduled serving pods to detect scale-to-zero completion."""
    kubectl_cmd = os.environ.get("KUBECTL_CMD", "kubectl")
    try:
        proc = subprocess.run(
            [
                kubectl_cmd,
                "--namespace",
                namespace,
                "get",
                "pods",
                "-l",
                "llm-d.ai/inferenceServing=true",
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None

    count = 0
    for pod in data.get("items", []):
        meta = pod.get("metadata") or {}
        labels = meta.get("labels") or {}
        model = labels.get("llm-d.ai/model", labels.get("app", ""))
        if model_filter and model and model != model_filter:
            continue
        phase = (pod.get("status") or {}).get("phase", "")
        if phase in ("Succeeded", "Failed"):
            continue
        if (pod.get("spec") or {}).get("nodeName"):
            count += 1
    return count


def _run_coordinator_http_mode(
    *,
    cfg: dict[str, Any],
    waves: list[dict[str, Any]],
    endpoint_url: str,
    model_name: str,
) -> tuple[list[dict[str, Any]], float, float]:
    """Submit waves via llm-d-coordinator HTTP async enqueue + poll /v1/requests/{id}."""
    transport = cfg.get("transport") or {}
    base_url = str(transport.get("coordinatorUrl") or endpoint_url).rstrip("/")
    api_path = str(cfg.get("apiPath") or "/v1/completions")
    poll_interval_s = float(transport.get("pollIntervalSeconds") or 2.0)
    grace_s = float(cfg.get("completionGraceSeconds") or 30.0)

    pending_lock = threading.Lock()
    records: dict[str, dict[str, Any]] = {}

    t0 = time.time()

    def _enqueue_one(wave: dict[str, Any], seq_idx: int) -> None:
        wave_name = str(wave.get("name") or "wave")
        cohort = str(wave.get("cohort") or wave_name)
        tenant = str(wave.get("tenant") or cfg.get("tenant") or "batch-default")
        deadline_offset = int(wave.get("deadlineOffsetSeconds") or 300)
        prompt_tokens = int(wave.get("promptTokens") or cfg.get("promptTokens") or 256)
        max_tokens = int(wave.get("maxTokens") or cfg.get("maxTokens") or 128)
        client_id = f"{wave_name}-{seq_idx:05d}-{secrets.token_hex(4)}"

        payload = build_openai_payload(
            api_path=api_path,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            max_tokens=max_tokens,
            req_id=client_id,
        )
        req_bytes = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-llm-d-async-mode": "enqueue",
            "x-llm-d-tenant": tenant,
            "x-llm-d-request-timeout-seconds": str(deadline_offset),
        }
        submitted = time.time()
        deadline_at = submitted + deadline_offset
        http_req = urllib.request.Request(
            f"{base_url}{api_path}", data=req_bytes, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(http_req, timeout=30) as resp:
                resp_body = json.loads(resp.read().decode("utf-8"))
                server_id = str(
                    resp_body.get("request_id") or resp_body.get("id") or client_id
                )
                with pending_lock:
                    records[server_id] = {
                        "id": server_id,
                        "wave": wave_name,
                        "cohort": cohort,
                        "submitted_at": submitted,
                        "deadline_at": deadline_at,
                        "prompt_tokens": prompt_tokens,
                        "max_tokens": max_tokens,
                        "done": False,
                    }
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
        ) as exc:
            now = time.time()
            with pending_lock:
                records[client_id] = {
                    **parse_result_envelope(
                        {
                            "id": client_id,
                            "status_code": 0,
                            "error_code": "ENQUEUE_ERROR",
                            "error_message": str(exc),
                        },
                        completed_at=now,
                        submitted_at=submitted,
                        deadline_at=deadline_at,
                    ),
                    "wave": wave_name,
                    "cohort": cohort,
                    "done": True,
                }

    # Schedule waves relative to t0
    wave_threads: list[threading.Thread] = []

    def _run_wave(wave: dict[str, Any]) -> None:
        offset_s = float(wave.get("offsetSeconds") or 0.0)
        wait_s = (t0 + offset_s) - time.time()
        if wait_s > 0:
            time.sleep(wait_s)
        count = int(wave.get("count") or 0)
        rate_rps = float(wave.get("rateRps") or 0.0)
        wave_start = time.time()
        for idx in range(count):
            if rate_rps > 0 and idx > 0:
                due = wave_start + (idx / rate_rps)
                sleep_for = due - time.time()
                if sleep_for > 0:
                    time.sleep(sleep_for)
            _enqueue_one(wave, idx)

    for w in waves:
        th = threading.Thread(target=_run_wave, args=(w,), daemon=True)
        th.start()
        wave_threads.append(th)

    # Poll /v1/requests/{id} until all waves finished and all requests resolved or expired
    while True:
        waves_alive = any(th.is_alive() for th in wave_threads)
        with pending_lock:
            unresolved = [r for r in records.values() if not r.get("done")]

        if not waves_alive and not unresolved:
            break

        now = time.time()
        for item in unresolved:
            rid = item["id"]
            if now > item["deadline_at"] + grace_s:
                parsed = parse_result_envelope(
                    {
                        "id": rid,
                        "status_code": 504,
                        "error_code": "DEADLINE_EXCEEDED",
                        "error_message": "Timed out polling coordinator result past deadline",
                    },
                    completed_at=now,
                    submitted_at=item["submitted_at"],
                    deadline_at=item["deadline_at"],
                )
                with pending_lock:
                    records[rid].update(parsed)
                    records[rid]["done"] = True
                continue

            poll_req = urllib.request.Request(
                f"{base_url}/v1/requests/{urllib.parse.quote(rid)}", method="GET"
            )
            try:
                with urllib.request.urlopen(poll_req, timeout=15) as resp:
                    if resp.status == 202:
                        continue
                    raw_body = resp.read().decode("utf-8", errors="replace")
                    done_ts = time.time()
                    parsed = parse_result_envelope(
                        {
                            "id": rid,
                            "status_code": resp.status,
                            "payload": raw_body,
                        },
                        completed_at=done_ts,
                        submitted_at=item["submitted_at"],
                        deadline_at=item["deadline_at"],
                        fallback_input_tokens=item["prompt_tokens"],
                        fallback_output_tokens=item["max_tokens"],
                    )
                    with pending_lock:
                        records[rid].update(parsed)
                        records[rid]["done"] = True
            except urllib.error.HTTPError as http_err:
                if http_err.code == 202:
                    continue
                done_ts = time.time()
                err_code = (
                    "DEADLINE_EXCEEDED"
                    if http_err.code == 504
                    else f"HTTP_{http_err.code}"
                )
                parsed = parse_result_envelope(
                    {
                        "id": rid,
                        "status_code": http_err.code,
                        "error_code": err_code,
                        "error_message": str(http_err),
                    },
                    completed_at=done_ts,
                    submitted_at=item["submitted_at"],
                    deadline_at=item["deadline_at"],
                )
                with pending_lock:
                    records[rid].update(parsed)
                    records[rid]["done"] = True
            except urllib.error.URLError:
                continue

        time.sleep(poll_interval_s)

    for th in wave_threads:
        th.join(timeout=5)

    t_drained = time.time()
    out = []
    for rec in records.values():
        clean = {
            k: v
            for k, v in rec.items()
            if k not in ("done", "prompt_tokens", "max_tokens")
        }
        out.append(clean)
    return out, t0, t_drained


def _run_redis_sortedset_mode(
    *,
    cfg: dict[str, Any],
    waves: list[dict[str, Any]],
    namespace: str,
    model_name: str,
) -> tuple[list[dict[str, Any]], float, float]:
    """Submit waves to Redis SortedSet via Lua and consume BRPOP result envelopes."""
    transport = cfg.get("transport") or {}
    default_redis = f"redis://redis.{namespace}.svc.cluster.local:6379/0"
    redis_url = str(
        transport.get("redisUrl")
        or os.environ.get("LLMDBENCH_ASYNC_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or default_redis
    )
    default_req_queue = str(transport.get("requestQueueName") or "request-sortedset")
    result_queue = str(transport.get("resultQueueName") or "result-list")
    clear_queues = bool(transport.get("clearQueuesBeforeRun", True))
    api_path = str(cfg.get("apiPath") or "/v1/completions")
    grace_s = float(cfg.get("completionGraceSeconds") or 30.0)

    admin_client = Resp2RedisClient(redis_url, timeout=15.0)
    if not admin_client.ping():
        raise RuntimeError(f"Redis PING failed against {redis_url}")

    if clear_queues:
        queues_to_clear = {default_req_queue, result_queue}
        for w in waves:
            if w.get("queueName"):
                queues_to_clear.add(str(w["queueName"]))
        admin_client.delete(*sorted(queues_to_clear))
    admin_client.close()

    total_expected = sum(int(w.get("count") or 0) for w in waves)
    pending_lock = threading.Lock()
    inflight_meta: dict[str, dict[str, Any]] = {}
    completed_records: dict[str, dict[str, Any]] = {}
    stop_consumer = threading.Event()

    def _consumer_loop() -> None:
        consumer = Resp2RedisClient(redis_url, timeout=10.0)
        try:
            while not stop_consumer.is_set():
                with pending_lock:
                    if len(completed_records) >= total_expected and total_expected > 0:
                        break
                try:
                    popped = consumer.brpop(result_queue, timeout_seconds=1)
                except (OSError, RedisProtocolError):
                    time.sleep(0.5)
                    consumer.close()
                    continue
                if popped is None:
                    continue
                _qname, raw_val = popped
                done_ts = time.time()
                try:
                    msg = json.loads(raw_val)
                except json.JSONDecodeError:
                    continue
                rid = str(msg.get("id") or "")
                with pending_lock:
                    meta = inflight_meta.get(rid)
                if meta is None:
                    # Stale result from an earlier run; ignore
                    continue
                rec = parse_result_envelope(
                    msg,
                    completed_at=done_ts,
                    submitted_at=meta["submitted_at"],
                    deadline_at=meta["deadline_at"],
                    fallback_input_tokens=meta["prompt_tokens"],
                    fallback_output_tokens=meta["max_tokens"],
                )
                rec["wave"] = meta["wave"]
                rec["cohort"] = meta["cohort"]
                rec["queue_name"] = meta["queue_name"]
                with pending_lock:
                    completed_records[rid] = rec
        finally:
            consumer.close()

    consumer_thread = threading.Thread(target=_consumer_loop, daemon=True)
    consumer_thread.start()

    t0 = time.time()

    def _run_wave(wave_idx: int, wave: dict[str, Any]) -> None:
        offset_s = float(wave.get("offsetSeconds") or 0.0)
        wait_s = (t0 + offset_s) - time.time()
        if wait_s > 0:
            time.sleep(wait_s)

        wave_name = str(wave.get("name") or f"wave-{wave_idx + 1}")
        cohort = str(wave.get("cohort") or wave_name)
        queue_name = str(wave.get("queueName") or default_req_queue)
        deadline_offset = int(wave.get("deadlineOffsetSeconds") or 300)
        count = int(wave.get("count") or 0)
        rate_rps = float(wave.get("rateRps") or 0.0)
        prompt_tokens = int(wave.get("promptTokens") or cfg.get("promptTokens") or 256)
        max_tokens = int(wave.get("maxTokens") or cfg.get("maxTokens") or 128)
        tenant = str(wave.get("tenant") or cfg.get("tenant") or "batch-default")

        producer = Resp2RedisClient(redis_url, timeout=15.0)
        try:
            wave_start = time.time()
            print(
                f"[async-batch] Launching wave '{wave_name}' (cohort={cohort}, "
                f"queue={queue_name}, count={count}, deadline=+{deadline_offset}s)",
                flush=True,
            )
            for idx in range(count):
                if rate_rps > 0 and idx > 0:
                    due = wave_start + (idx / rate_rps)
                    sleep_for = due - time.time()
                    if sleep_for > 0:
                        time.sleep(sleep_for)

                req_id = f"{wave_name}-{idx:05d}-{secrets.token_hex(4)}"
                now_f = time.time()
                created_unix = int(now_f)
                deadline_unix = int(math.ceil(now_f + deadline_offset))
                payload = build_openai_payload(
                    api_path=api_path,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    max_tokens=max_tokens,
                    req_id=req_id,
                )
                meta_entry = {
                    "wave": wave_name,
                    "cohort": cohort,
                    "queue_name": queue_name,
                    "submitted_at": now_f,
                    "deadline_at": float(deadline_unix),
                    "prompt_tokens": prompt_tokens,
                    "max_tokens": max_tokens,
                }
                with pending_lock:
                    inflight_meta[req_id] = meta_entry

                keys, argv, _token = build_sortedset_enqueue_command(
                    req_id=req_id,
                    created_unix=created_unix,
                    deadline_unix=deadline_unix,
                    payload=payload,
                    request_queue_name=queue_name,
                    result_queue_name=result_queue,
                    endpoint=api_path,
                    model_name=model_name,
                    metadata={"wave": wave_name, "cohort": cohort, "tenant": tenant},
                    now_epoch=now_f,
                )
                try:
                    producer.eval_script(SUBMIT_REQUEST_LUA, keys, argv)
                except (OSError, RedisProtocolError) as exc:
                    err_ts = time.time()
                    rec = parse_result_envelope(
                        {
                            "id": req_id,
                            "status_code": 0,
                            "error_code": "REDIS_ENQUEUE_ERROR",
                            "error_message": str(exc),
                        },
                        completed_at=err_ts,
                        submitted_at=now_f,
                        deadline_at=float(deadline_unix),
                    )
                    rec["wave"] = wave_name
                    rec["cohort"] = cohort
                    rec["queue_name"] = queue_name
                    with pending_lock:
                        completed_records[req_id] = rec
        finally:
            producer.close()

    wave_threads: list[threading.Thread] = []
    for idx, w in enumerate(waves):
        th = threading.Thread(target=_run_wave, args=(idx, w), daemon=True)
        th.start()
        wave_threads.append(th)

    for th in wave_threads:
        th.join()

    # Wait until all enqueued requests complete or pass max_deadline + grace_s
    with pending_lock:
        max_deadline = max(
            (m["deadline_at"] for m in inflight_meta.values()),
            default=time.time(),
        )

    last_log = 0.0
    while True:
        now = time.time()
        with pending_lock:
            done_count = len(completed_records)
            total_submitted = len(inflight_meta)
        if done_count >= total_submitted:
            break
        if now > max_deadline + grace_s:
            print(
                f"[async-batch] Completion timeout reached ({done_count}/{total_submitted} "
                f"received by max_deadline + {grace_s}s); marking remaining as DEADLINE_EXCEEDED.",
                flush=True,
            )
            break
        if now - last_log >= 10.0:
            print(
                f"[async-batch] Progress: {done_count}/{total_submitted} results collected...",
                flush=True,
            )
            last_log = now
        time.sleep(1.0)

    stop_consumer.set()
    consumer_thread.join(timeout=5.0)
    t_drained = time.time()

    with pending_lock:
        for rid, meta in inflight_meta.items():
            if rid not in completed_records:
                rec = parse_result_envelope(
                    {
                        "id": rid,
                        "status_code": 504,
                        "error_code": "DEADLINE_EXCEEDED",
                        "error_message": "No result received before deadline + grace window",
                    },
                    completed_at=t_drained,
                    submitted_at=meta["submitted_at"],
                    deadline_at=meta["deadline_at"],
                )
                rec["wave"] = meta["wave"]
                rec["cohort"] = meta["cohort"]
                rec["queue_name"] = meta["queue_name"]
                completed_records[rid] = rec
        ordered_records = [completed_records[rid] for rid in inflight_meta]

    return ordered_records, t0, t_drained


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    on_time_recs = [r for r in records if r.get("on_time")]
    succeeded_http = [r for r in records if r.get("succeeded_http")]
    exceeded = [r for r in records if r.get("exceeded_deadline")]
    failed = [
        r for r in records if not r.get("on_time") and not r.get("exceeded_deadline")
    ]

    turnarounds = [float(r["turnaround_s"]) for r in succeeded_http]
    slacks = [float(r["slack_s"]) for r in records]
    on_time_in_toks = sum(int(r.get("input_tokens") or 0) for r in on_time_recs)
    on_time_out_toks = sum(int(r.get("output_tokens") or 0) for r in on_time_recs)
    total_in_toks = sum(int(r.get("input_tokens") or 0) for r in succeeded_http)
    total_out_toks = sum(int(r.get("output_tokens") or 0) for r in succeeded_http)

    return {
        "total_requests": total,
        "succeeded_http_requests": len(succeeded_http),
        "on_time_requests": len(on_time_recs),
        "exceeded_deadline_requests": len(exceeded),
        "failed_requests": len(failed),
        "deadline_attainment_rate": round(len(on_time_recs) / total, 6)
        if total
        else 0.0,
        "on_time_input_tokens": on_time_in_toks,
        "on_time_output_tokens": on_time_out_toks,
        "total_input_tokens": total_in_toks,
        "total_output_tokens": total_out_toks,
        "turnaround_s": summarize_series(turnarounds, "s"),
        "slack_s": summarize_series(slacks, "s"),
    }


def run_workload(config_path: Path, results_dir: Path) -> int:
    """Execute an async-batch profile and persist results into results_dir."""
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    results_dir.mkdir(parents=True, exist_ok=True)

    namespace = os.environ.get("LLMDBENCH_VLLM_COMMON_NAMESPACE", "default")
    endpoint_url = os.environ.get(
        "LLMDBENCH_HARNESS_STACK_ENDPOINT_URL", "http://localhost:8000"
    )
    model_name = str(
        cfg.get("model")
        or os.environ.get("LLMDBENCH_DEPLOY_CURRENT_MODEL")
        or "default"
    )
    model_filter = os.environ.get("LLMDBENCH_HARNESS_STACK_NAME", "")

    waves = cfg.get("waves") or []
    if not waves:
        raise ValueError(f"Profile {config_path} defines no 'waves'")

    transport_mode = str((cfg.get("transport") or {}).get("mode") or "redis-sortedset")
    print(
        f"[async-batch] Starting workload '{cfg.get('name', config_path.stem)}' "
        f"(mode={transport_mode}, waves={len(waves)}, model={model_name})",
        flush=True,
    )

    if transport_mode == "coordinator-http":
        records, t0, t_drained = _run_coordinator_http_mode(
            cfg=cfg,
            waves=waves,
            endpoint_url=endpoint_url,
            model_name=model_name,
        )
    elif transport_mode == "redis-sortedset":
        records, t0, t_drained = _run_redis_sortedset_mode(
            cfg=cfg,
            waves=waves,
            namespace=namespace,
            model_name=model_name,
        )
    else:
        raise ValueError(f"Unsupported transport.mode: {transport_mode!r}")

    # Optional scale-down wait so cooldown GPU-hours are captured fairly
    accounting = cfg.get("accounting") or {}
    wait_for_scale_down = bool(accounting.get("waitForScaleDown", False))
    settled_target = int(accounting.get("settledReplicasTarget", 0))
    scale_down_timeout_s = float(accounting.get("scaleDownTimeoutSeconds", 300.0))
    window_duration_s = float(accounting.get("windowDurationSeconds", 0.0))

    scale_down_settled: bool | None = None
    if wait_for_scale_down:
        print(
            f"[async-batch] Waiting up to {scale_down_timeout_s:.0f}s for serving pods "
            f"to settle to <= {settled_target} scheduled replica(s)...",
            flush=True,
        )
        sd_deadline = time.time() + scale_down_timeout_s
        scale_down_settled = False
        while time.time() <= sd_deadline:
            current_sched = _count_scheduled_serving_pods(namespace, model_filter)
            if current_sched is None:
                # Local / nok8s environment without kubectl access
                scale_down_settled = None
                break
            if current_sched <= settled_target:
                scale_down_settled = True
                print(
                    f"[async-batch] Serving pool settled at {current_sched} scheduled replica(s).",
                    flush=True,
                )
                break
            time.sleep(5.0)

    # Duty-cycle window hold so Always-On vs Scale-to-Zero compare over identical [t0, t0 + T_window]
    if window_duration_s > 0:
        target_end = t0 + window_duration_s
        remaining = target_end - time.time()
        if remaining > 0:
            print(
                f"[async-batch] Holding {remaining:.1f}s to complete "
                f"{window_duration_s:.0f}s duty-cycle accounting window...",
                flush=True,
            )
            time.sleep(remaining)

    t_end = time.time()

    by_cohort: dict[str, list[dict[str, Any]]] = {}
    by_wave: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        by_cohort.setdefault(str(rec.get("cohort", "default")), []).append(rec)
        by_wave.setdefault(str(rec.get("wave", "default")), []).append(rec)

    aggregate = _summarize_records(records)
    slo_gate = {
        "min_deadline_attainment": float(
            (cfg.get("slo") or {}).get("minDeadlineAttainment", 0.99)
        )
    }

    results_payload = {
        "harness": "async-batch",
        "schema_version": "1.0",
        "workload_name": cfg.get("name", config_path.stem),
        "transport_mode": transport_mode,
        "model": model_name,
        "namespace": namespace,
        "window_start_epoch": round(t0, 3),
        "drain_completed_epoch": round(t_drained, 3),
        "window_end_epoch": round(t_end, 3),
        "drain_duration_seconds": round(max(t_drained - t0, 0.0), 3),
        "accounting_window_seconds": round(max(t_end - t0, 0.0), 3),
        "configured_window_duration_seconds": window_duration_s,
        "wait_for_scale_down": wait_for_scale_down,
        "scale_down_settled": scale_down_settled,
        "slo_gate": slo_gate,
        "aggregate": aggregate,
        "cohorts": {k: _summarize_records(v) for k, v in by_cohort.items()},
        "waves": {k: _summarize_records(v) for k, v in by_wave.items()},
    }

    (results_dir / "results.json").write_text(
        json.dumps(results_payload, indent=2), encoding="utf-8"
    )
    (results_dir / "per_request_lifecycle_metrics.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )

    print("========== ASYNC-BATCH SUMMARY ==========", flush=True)
    print(
        f"Total requests: {aggregate['total_requests']} | "
        f"On-time: {aggregate['on_time_requests']} "
        f"({aggregate['deadline_attainment_rate'] * 100:.2f}%) | "
        f"Exceeded deadline: {aggregate['exceeded_deadline_requests']} | "
        f"Failed: {aggregate['failed_requests']}",
        flush=True,
    )
    print(
        f"Drain time: {results_payload['drain_duration_seconds']:.1f}s | "
        f"Accounting window: {results_payload['accounting_window_seconds']:.1f}s",
        flush=True,
    )
    return 0


def finalize_goodput_summary(results_dir: Path) -> dict[str, Any]:
    """Build and write async_goodput_summary.json after process_metrics.py runs."""
    results_file = results_dir / "results.json"
    if not results_file.exists():
        raise FileNotFoundError(f"Missing {results_file}")
    results_data = json.loads(results_file.read_text(encoding="utf-8"))

    processed_dir = results_dir / "metrics" / "processed"
    replica_status: dict[str, Any] = {}
    if (processed_dir / "replica_status.json").exists():
        replica_status = json.loads(
            (processed_dir / "replica_status.json").read_text(encoding="utf-8")
        )
    pod_startup: dict[str, Any] = {}
    if (processed_dir / "pod_startup_times.json").exists():
        pod_startup = json.loads(
            (processed_dir / "pod_startup_times.json").read_text(encoding="utf-8")
        )

    summary = compute_async_goodput_summary(
        results_data,
        replica_status_data=replica_status,
        pod_startup_data=pod_startup,
    )
    out_path = results_dir / "async_goodput_summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="llm-d-benchmark async-batch harness")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to rendered async-batch profile YAML",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Experiment results directory",
    )
    parser.add_argument(
        "--finalize-only",
        action="store_true",
        help="Join existing results.json + metrics/processed/* into async_goodput_summary.json",
    )
    args = parser.parse_args(argv)

    results_dir = args.results_dir or Path(
        os.environ.get("LLMDBENCH_RUN_EXPERIMENT_RESULTS_DIR", ".")
    )
    if args.finalize_only:
        summary = finalize_goodput_summary(results_dir)
        print(
            f"[async-batch] Goodput summary ({summary['verdict']}): "
            f"request_goodput_per_gpu_hour={summary['request_goodput_per_gpu_hour']}, "
            f"output_token_goodput_per_gpu_hour={summary['output_token_goodput_per_gpu_hour']}, "
            f"allocated_gpu_hours={summary['allocated_gpu_hours']} ({summary['accelerator_mode']})"
        )
        return 0

    if args.config is not None:
        config_path = args.config
    else:
        workspace_dir = Path(
            os.environ.get("LLMDBENCH_RUN_WORKSPACE_DIR", "/workspace")
        )
        workload_name = os.environ.get(
            "LLMDBENCH_RUN_EXPERIMENT_HARNESS_WORKLOAD_NAME", "sanity_async.yaml"
        )
        candidate = results_dir / workload_name
        config_path = (
            candidate
            if candidate.exists()
            else workspace_dir / "profiles" / "async-batch" / workload_name
        )

    return run_workload(config_path, results_dir)


if __name__ == "__main__":
    sys.exit(main())
