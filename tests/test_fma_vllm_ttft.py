"""calculate_vllm_ttft times from sending the request, as vLLM's benchmark client does.

``requests.post(stream=True)`` returns once the headers arrive, and a streaming
server sends those before the first token.
"""

import os
import sys

# fma_functions.py uses bare imports (``from dpc_log_parser import ...``) that
# assume ``workload/harnesses`` is on the path, as in the harness container.
_HARNESS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "workload", "harnesses")
)
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

import fma_functions as f  # noqa: E402


def test_clock_starts_before_the_request_is_sent(monkeypatch):
    now = [0.0]

    class _Response:
        def __enter__(self):
            now[0] += 0.5  # request sent, headers back
            return self

        def __exit__(self, *exc):
            return False

        def iter_lines(self):
            now[0] += 0.25  # first token
            yield b'data: {"choices": [{"text": "Once"}]}'
            yield b"data: [DONE]"

    monkeypatch.setattr(f.time, "perf_counter", lambda: now[0])
    monkeypatch.setattr(f.requests, "post", lambda *a, **k: _Response())

    assert f.calculate_vllm_ttft("http://vllm:8000", "m", 5) == 0.75
