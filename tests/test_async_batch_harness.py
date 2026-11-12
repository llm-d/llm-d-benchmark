"""Unit tests for async_batch.py RESP2 Redis client, SortedSet Lua wire format, and Goodput computation."""

from __future__ import annotations

import importlib.util
import json
import socketserver
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASYNC_BATCH_PATH = PROJECT_ROOT / "workload" / "harnesses" / "async_batch.py"


def _load_async_batch():
    spec = importlib.util.spec_from_file_location(
        "llmdbench_async_batch", ASYNC_BATCH_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ab = _load_async_batch()


class _MockRedisState:
    def __init__(self):
        self.lock = threading.Lock()
        self.strings: dict[str, str] = {}
        self.ttl_ms: dict[str, int] = {}
        self.expire_at: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.lists: dict[str, list[str]] = {}
        self.eval_calls: list[list[str]] = []


def _read_resp_frame(rfile):
    line = rfile.readline()
    if not line:
        return None
    prefix = line[:1]
    body = line[1:].rstrip(b"\r\n")
    if prefix == b"*":
        count = int(body)
        return [_read_resp_frame(rfile) for _ in range(count)]
    if prefix == b"$":
        length = int(body)
        data = rfile.read(length)
        rfile.read(2)  # \r\n
        return data.decode("utf-8")
    return body.decode("utf-8")


class _MockRedisHandler(socketserver.StreamRequestHandler):
    def handle(self):
        state: _MockRedisState = self.server.redis_state  # type: ignore[attr-defined]
        while True:
            cmd = _read_resp_frame(self.rfile)
            if cmd is None:
                break
            op = cmd[0].upper()
            with state.lock:
                if op == "PING":
                    self.wfile.write(b"+PONG\r\n")
                elif op == "DEL":
                    removed = 0
                    for k in cmd[1:]:
                        removed += int(
                            k in state.strings or k in state.zsets or k in state.lists
                        )
                        state.strings.pop(k, None)
                        state.zsets.pop(k, None)
                        state.lists.pop(k, None)
                    self.wfile.write(f":{removed}\r\n".encode())
                elif op == "EVAL":
                    state.eval_calls.append(cmd)
                    # Emulate SUBMIT_REQUEST_LUA from producer/redis_sortedset_producer.go:46-57
                    numkeys = int(cmd[2])
                    keys = cmd[3 : 3 + numkeys]
                    argv = cmd[3 + numkeys :]
                    seq = int(state.strings.get(keys[0], "0")) + 1
                    state.strings[keys[0]] = str(seq)
                    state.expire_at[keys[0]] = int(argv[2])
                    state.strings.pop(keys[1], None)
                    state.strings[keys[2]] = argv[0]
                    state.ttl_ms[keys[2]] = int(argv[1])
                    score = float(argv[5]) + min(seq, 2097151) / 2097152.0
                    member = argv[3] + str(seq) + argv[4]
                    state.zsets.setdefault(keys[3], {})[member] = score
                    self.wfile.write(f":{seq}\r\n".encode())
                elif op == "ZCARD":
                    n = len(state.zsets.get(cmd[1], {}))
                    self.wfile.write(f":{n}\r\n".encode())
                elif op == "LLEN":
                    n = len(state.lists.get(cmd[1], []))
                    self.wfile.write(f":{n}\r\n".encode())
                elif op == "LPUSH":
                    lst = state.lists.setdefault(cmd[1], [])
                    for val in cmd[2:]:
                        lst.insert(0, val)
                    self.wfile.write(f":{len(lst)}\r\n".encode())
                elif op == "BRPOP":
                    key = cmd[1]
                    lst = state.lists.get(key, [])
                    if not lst:
                        self.wfile.write(b"*-1\r\n")
                    else:
                        val = lst.pop()  # pop from tail matching LPUSH + BRPOP FIFO
                        key_b = key.encode("utf-8")
                        val_b = val.encode("utf-8")
                        self.wfile.write(
                            b"*2\r\n$"
                            + str(len(key_b)).encode()
                            + b"\r\n"
                            + key_b
                            + b"\r\n$"
                            + str(len(val_b)).encode()
                            + b"\r\n"
                            + val_b
                            + b"\r\n"
                        )
                else:
                    self.wfile.write(f"-ERR unsupported {op}\r\n".encode())
                self.wfile.flush()


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def test_resp2_redis_sortedset_wire_compatibility():
    """Verify exact KEYS/ARGV/score/JSON wire format matching redis_sortedset_producer.go."""
    state = _MockRedisState()
    server = _ThreadedTCPServer(("127.0.0.1", 0), _MockRedisHandler)
    server.redis_state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address

    try:
        client = ab.Resp2RedisClient(f"redis://{host}:{port}/0")
        assert client.ping() is True

        keys, argv, token = ab.build_sortedset_enqueue_command(
            req_id="req-urgent-001",
            created_unix=1770000000,
            deadline_unix=1770000150,
            payload={"model": "Qwen/Qwen3-32B", "prompt": "Hello", "max_tokens": 64},
            request_queue_name="request-sortedset",
            result_queue_name="result-list",
            endpoint="/v1/completions",
            model_name="Qwen/Qwen3-32B",
            metadata={"cohort": "urgent"},
            now_epoch=1770000000.0,
        )
        assert keys == [
            "request-seq:request-sortedset:1770000150",
            "request-cancel:req-urgent-001",
            "request-active:req-urgent-001",
            "request-sortedset",
        ]
        seq = client.eval_script(ab.SUBMIT_REQUEST_LUA, keys, argv)
        assert seq == 1
        assert client.zcard("request-sortedset") == 1
        assert state.strings["request-active:req-urgent-001"] == token
        assert state.ttl_ms["request-active:req-urgent-001"] == 150000

        # Verify exact SortedSet member JSON & deadline+seq score wire format
        members = list(state.zsets["request-sortedset"].items())
        assert len(members) == 1
        member_json, score = members[0]
        assert score == pytest.approx(ab.compute_queue_score(1770000150, 1))
        internal_req = json.loads(member_json)
        assert internal_req["internal"]["request_token"] == token
        assert internal_req["internal"]["enqueue_seq"] == 1
        assert internal_req["internal"]["request_queue_name"] == "request-sortedset"
        assert internal_req["internal"]["result_queue_name"] == "result-list"
        assert internal_req["request_kind"] == "plain"
        assert internal_req["data"]["id"] == "req-urgent-001"
        assert internal_req["data"]["deadline"] == 1770000150
        assert internal_req["data"]["payload"]["model"] == "Qwen/Qwen3-32B"
        assert internal_req["data"]["metadata"]["cohort"] == "urgent"

        # Simulate llm-d-async LPUSH result envelope and verify FIFO BRPOP + deadline check
        envelope = json.dumps(
            {
                "id": "req-urgent-001",
                "status_code": 200,
                "payload": json.dumps(
                    {
                        "choices": [{"text": " world"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 64},
                    }
                ),
            }
        )
        client.execute("LPUSH", "result-list", envelope)
        popped = client.brpop("result-list", timeout_seconds=1)
        assert popped is not None
        q_name, raw_json = popped
        assert q_name == "result-list"
        parsed = ab.parse_result_envelope(
            raw_json,
            completed_at=1770000042.0,
            submitted_at=1770000000.0,
            deadline_at=1770000150.0,
        )
        assert parsed["id"] == "req-urgent-001"
        assert parsed["status_code"] == 200
        assert parsed["on_time"] is True
        assert parsed["exceeded_deadline"] is False
        assert parsed["slack_s"] == pytest.approx(108.0)
        assert parsed["output_tokens"] == 64
        assert parsed["input_tokens"] == 5

        # Late HTTP 200 response after deadline is strictly rejected from on_time
        late_parsed = ab.parse_result_envelope(
            raw_json,
            completed_at=1770000155.0,
            submitted_at=1770000000.0,
            deadline_at=1770000150.0,
        )
        assert late_parsed["on_time"] is False
        assert late_parsed["exceeded_deadline"] is True
        client.close()
    finally:
        server.shutdown()
        server.server_close()


def test_compute_async_goodput_summary_joins_gpu_hours_and_enforces_attainment_gate():
    """Verify strict deadline attainment gate, Goodput, and calibrated R/C computation."""
    results_data = {
        "accounting_window_seconds": 250.0,
        "scale_down_settled": True,
        "slo_gate": {"min_deadline_attainment": 0.99},
        "aggregate": {
            "total_requests": 4,
            "on_time_requests": 2,
            "exceeded_deadline_requests": 2,
            "failed_requests": 0,
            "on_time_input_tokens": 256,
            "on_time_output_tokens": 128,
            "slack_s": {"p10": -5.0, "p50": 45.0},
            "turnaround_s": {"mean": 48.0, "p95": 110.0},
        },
    }
    replica_status = {
        "resource_integral": {
            "window_seconds": 250.0,
            "allocated_gpu_hours": 0.1,
            "ready_gpu_hours": 300.0 / 3600.0,
            "allocated_pod_hours": 0.1,
            "ready_pod_hours": 300.0 / 3600.0,
            "overhead_gpu_fraction": 1.0 / 6.0,
            "accelerator_mode": "physical_gpu",
        }
    }
    pod_startup = {"aggregate": {"p90": 55.0, "mean": 47.5}}

    summary = ab.compute_async_goodput_summary(
        results_data=results_data,
        replica_status_data=replica_status,
        pod_startup_data=pod_startup,
        min_deadline_attainment=0.99,
    )

    assert summary["total_requests"] == 4
    assert summary["on_time_requests"] == 2
    assert summary["exceeded_deadline_requests"] == 2
    assert summary["deadline_attainment_rate"] == pytest.approx(0.5)
    assert summary["verdict"] == "fail"
    # 2 good requests / 0.1 GPU-hr = 20 good_req / GPU-hr
    assert summary["request_goodput_per_gpu_hour"] == pytest.approx(20.0)
    assert summary["request_goodput_per_gpu_min"] == pytest.approx(
        20.0 / 60.0, abs=1e-4
    )
    # 128 good output tokens / 0.1 GPU-hr = 1280 good_tok / GPU-hr
    assert summary["output_token_goodput_per_gpu_hour"] == pytest.approx(1280.0)
    assert summary["calibrated_cold_start_p90_s"] == pytest.approx(55.0)
    assert summary["calibrated_replica_rps"] == pytest.approx(2 / 300.0, abs=1e-6)
    assert summary["scale_down_settled"] is True
