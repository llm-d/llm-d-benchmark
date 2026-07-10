"""Tests for ``reset_caches_pods`` in utilities/endpoint.py.

The helper makes two ``cmd.kube`` calls: first a ``get pods`` to discover
serving-pod IPs, then a single batched ephemeral curl pod that POSTs each of
/reset_prefix_cache, /reset_mm_cache, and /reset_encoder_cache to every IP.
All failures are non-fatal -- the helper returns a list of warning strings
and never raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from llmdbenchmark.utilities.endpoint import (
    reset_caches_pods,
    _CACHE_RESET_ENDPOINTS,
)


def _result(stdout: str = "", success: bool = True, dry_run: bool = False):
    r = MagicMock()
    r.success = success
    r.stdout = stdout
    r.stderr = ""
    r.dry_run = dry_run
    return r


def _cmd(side_effect):
    cmd = MagicMock()
    cmd.kube = MagicMock(side_effect=side_effect)
    return cmd


def test_posts_all_endpoints_to_every_pod_ip_in_one_batched_pod():
    # get pods -> two IPs; then the curl pod succeeds with all-2xx statuses.
    # kubectl -o jsonpath={.items[*].status.podIP} prints IPs space-separated.
    list_result = _result(stdout="10.0.0.1 10.0.0.2")
    ok = "\n".join(["== x ==\n\n200"] * (2 * len(_CACHE_RESET_ENDPOINTS)))
    curl_result = _result(stdout=ok)
    cmd = _cmd([list_result, curl_result])

    warns = reset_caches_pods(cmd, "bench", "my-model", 8000, plan_config=None)

    assert warns == []
    # Two kube calls: list, then the ephemeral curl pod.
    assert cmd.kube.call_count == 2
    # The jsonpath handed to `get pods` must be space-free -- cmd.kube joins
    # argv with spaces into a shell string, so a spaced `{range ...}` template
    # would be word-split and mis-read by kubectl as a positional pod name.
    list_args = cmd.kube.call_args_list[0].args
    jsonpath = next(a for a in list_args if str(a).startswith("jsonpath="))
    assert " " not in jsonpath
    curl_args = cmd.kube.call_args_list[1].args
    assert curl_args[0] == "run"
    joined = " ".join(str(a) for a in curl_args)
    # Both IPs and all three cache endpoints appear in the single command.
    assert "10.0.0.1" in joined
    assert "10.0.0.2" in joined
    assert ":8000$ep" in joined
    for ep in _CACHE_RESET_ENDPOINTS:
        assert ep in joined
    assert "/reset_prefix_cache" in joined
    assert "/reset_mm_cache" in joined
    assert "/reset_encoder_cache" in joined


def test_empty_model_label_warns_and_makes_no_calls():
    cmd = _cmd([])  # no kube results should be consumed
    warns = reset_caches_pods(cmd, "bench", "", 8000)
    assert len(warns) == 1
    assert "no model label" in warns[0].lower()
    cmd.kube.assert_not_called()


def test_no_pods_found_warns_and_skips_curl():
    cmd = _cmd([_result(stdout="\n")])  # list returns nothing usable
    warns = reset_caches_pods(cmd, "bench", "my-model", 8000)
    assert len(warns) == 1
    assert "no running vllm pods" in warns[0].lower()
    # Only the list call happened; no curl pod.
    assert cmd.kube.call_count == 1


def test_404_status_is_warned_not_raised():
    list_result = _result(stdout="10.0.0.1\n")
    # One endpoint 404s (dev mode off), others would too -- surface a warning.
    curl_result = _result(stdout="== a ==\n\n404\n== b ==\n\n404\n== c ==\n\n404")
    cmd = _cmd([list_result, curl_result])

    warns = reset_caches_pods(cmd, "bench", "my-model", 8000)

    assert len(warns) == 1
    assert "404" in warns[0]
    assert "VLLM_SERVER_DEV_MODE" in warns[0]


def test_list_failure_is_non_fatal():
    cmd = _cmd([_result(success=False)])
    warns = reset_caches_pods(cmd, "bench", "my-model", 8000)
    assert len(warns) == 1
    assert "failed to list" in warns[0].lower()
    assert cmd.kube.call_count == 1


def test_dry_run_list_short_circuits_clean():
    cmd = _cmd([_result(dry_run=True)])
    warns = reset_caches_pods(cmd, "bench", "my-model", 8000)
    assert warns == []
    assert cmd.kube.call_count == 1


def test_logger_receives_warnings():
    logger = MagicMock()
    cmd = _cmd([_result(stdout="\n")])
    reset_caches_pods(cmd, "bench", "my-model", 8000, logger=logger)
    assert logger.log_warning.called
