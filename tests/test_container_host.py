"""Tests for the nok8s container-runtime connection target (``ContainerHost``).

``nok8s.connection`` is the one knob that turns a local nok8s standup into a
remote one, and five steps read it, so these tests pin two things:

1. The *local* default produces byte-identical commands to the pre-connection
   code -- no flags, no ssh wrapper, no scp. A regression there breaks every
   existing single-host user.
2. A remote value produces commands that resolve on the **daemon** host, not the
   client: the runtime flag goes before the subcommand, probes are wrapped in
   ssh, and bind-mount sources are pushed with scp.
"""

from __future__ import annotations

import pytest

from llmdbenchmark.utilities.container_host import (
    ContainerHost,
    ContainerHostError,
    expand_remote_path,
)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "connection",
    ["", None, "localhost", "LOCALHOST", "local", "127.0.0.1", "::1"],
)
def test_local_forms_parse_as_local(connection) -> None:
    host = ContainerHost.parse(connection)
    assert host.is_remote is False
    assert host.runtime_args() == ""
    assert host.url == ""


@pytest.mark.parametrize(
    "connection", ["unix:///var/run/docker.sock", "/run/podman.sock"]
)
def test_socket_paths_stay_local(connection: str) -> None:
    """A socket path is the local runtime, not a new transport to invent."""
    assert ContainerHost.parse(connection).is_remote is False


def test_bare_ip_is_read_as_ssh() -> None:
    """The headline case: `connection: 10.0.0.7` and nothing else."""
    host = ContainerHost.parse("10.0.0.7")
    assert host.is_remote is True
    assert host.host == "10.0.0.7"
    assert host.user == ""
    assert host.destination == "10.0.0.7"


def test_user_host_port_and_socket_are_parsed() -> None:
    host = ContainerHost.parse(
        "ssh://bench@node1:2222/run/user/1000/podman/podman.sock", runtime="podman"
    )
    assert (host.user, host.host, host.port) == ("bench", "node1", 2222)
    assert host.socket == "/run/user/1000/podman/podman.sock"
    assert host.destination == "bench@node1"
    assert host.url == "ssh://bench@node1:2222/run/user/1000/podman/podman.sock"


def test_docker_url_gets_the_default_socket_path() -> None:
    """docker needs an explicit socket in the URL; podman resolves its own."""
    assert ContainerHost.parse("node1").url == "ssh://node1/var/run/docker.sock"
    assert ContainerHost.parse("node1", runtime="podman").url == "ssh://node1"


def test_tcp_is_refused_with_the_reason_and_the_alternative() -> None:
    """An unauthenticated daemon socket is root on the node; say so."""
    with pytest.raises(ContainerHostError) as exc:
        ContainerHost.parse("tcp://10.0.0.7:2375")
    message = str(exc.value)
    assert "root" in message
    assert "ssh://" in message


@pytest.mark.parametrize("connection", ["http://node1", "https://node1", "vsock://x"])
def test_other_schemes_are_refused(connection: str) -> None:
    with pytest.raises(ContainerHostError):
        ContainerHost.parse(connection)


def test_invalid_port_is_a_container_host_error_not_a_valueerror() -> None:
    """urlparse defers port validation to attribute access; catch it there."""
    with pytest.raises(ContainerHostError) as exc:
        ContainerHost.parse("ssh://node1:notaport")
    assert "port" in str(exc.value)


def test_scheme_with_no_host_is_refused() -> None:
    with pytest.raises(ContainerHostError):
        ContainerHost.parse("ssh://")


# ---------------------------------------------------------------------------
# command construction
# ---------------------------------------------------------------------------


def test_local_commands_are_unchanged() -> None:
    """The local path must be exactly what it was before connections existed."""
    host = ContainerHost.parse("localhost")
    assert host.runtime_cmd("rm", "-f", "envoy") == "docker rm -f envoy"
    assert host.shell("curl -fsS http://localhost:8081") == (
        "curl -fsS http://localhost:8081"
    )
    assert host.runtime_env() == ""


def test_docker_uses_dash_h_before_the_subcommand() -> None:
    """`docker run -H ...` is not a thing: the flag has to precede the verb."""
    cmd = ContainerHost.parse("node1").runtime_cmd("run", "-d", "--name", "epp")
    assert cmd.startswith("docker -H ")
    assert cmd.index("-H ") < cmd.index(" run ")
    assert "ssh://node1/var/run/docker.sock" in cmd


def test_podman_uses_url_and_identity() -> None:
    host = ContainerHost.parse("node1", runtime="podman", identity="/keys/id_ed25519")
    cmd = host.runtime_cmd("ps")
    assert cmd.startswith("podman --url ")
    assert "--identity /keys/id_ed25519" in cmd
    assert cmd.endswith(" ps")


def test_runtime_env_matches_the_runtime() -> None:
    assert "DOCKER_HOST=" in ContainerHost.parse("node1").runtime_env()
    podman = ContainerHost.parse("node1", runtime="podman", identity="/k/id")
    assert "CONTAINER_HOST=" in podman.runtime_env()
    assert "CONTAINER_SSHKEY=" in podman.runtime_env()


def test_shell_runs_the_command_on_the_daemon_host() -> None:
    """A readiness probe for `localhost` must mean the node, not the client."""
    host = ContainerHost.parse("bench@node1")
    wrapped = host.shell("curl -fsS http://localhost:8081/v1/models")
    assert wrapped.startswith("ssh ")
    assert " bench@node1 " in wrapped
    # The whole remote command is one quoted argument, so the client shell does
    # not try to interpret it.
    assert wrapped.endswith("'curl -fsS http://localhost:8081/v1/models'")


def test_shell_is_non_interactive_by_default() -> None:
    """A standup blocked on a passphrase prompt looks like a hang."""
    wrapped = ContainerHost.parse("node1").shell("true")
    assert "BatchMode=yes" in wrapped
    assert "ConnectTimeout=10" in wrapped


def test_ssh_args_replace_the_defaults() -> None:
    host = ContainerHost.parse("node1", ssh_args=["-o", "StrictHostKeyChecking=yes"])
    wrapped = host.shell("true")
    assert "StrictHostKeyChecking=yes" in wrapped
    assert "BatchMode=yes" not in wrapped


def test_port_flag_differs_between_ssh_and_scp() -> None:
    """scp spells the port -P; ssh spells it -p. Mixing them silently fails."""
    host = ContainerHost.parse("node1:2222")
    assert " -p 2222 " in host.shell("true")
    assert " -P 2222 " in host.push_dir("/local", "/remote")


def test_push_dir_creates_the_target_and_copies_contents() -> None:
    """Contents, not the directory: a re-run must overwrite, not nest."""
    pushed = ContainerHost.parse("bench@node1").push_dir("/local/stage", "/remote/ws")
    assert "mkdir -p /remote/ws" in pushed
    assert "scp " in pushed and " -r " in pushed
    assert "/local/stage/." in pushed
    assert "bench@node1:/remote/ws/" in pushed


def test_push_file_creates_the_parent_directory() -> None:
    pushed = ContainerHost.parse("node1").push_file("/local/x.sh", "/remote/d/x.sh")
    assert "mkdir -p /remote/d" in pushed
    assert "node1:/remote/d/x.sh" in pushed


def test_pull_dir_brings_results_back_to_the_client() -> None:
    pulled = ContainerHost.parse("node1").pull_dir("/remote/results", "/local/results")
    assert pulled.startswith("mkdir -p /local/results")
    assert "node1:/remote/results/." in pulled


def test_local_transfers_are_plain_copies() -> None:
    host = ContainerHost.parse("localhost")
    assert host.push_dir("/a", "/b") == "cp -a /a/. /b/"
    assert host.pull_dir("/a", "/b") == "cp -a /a/. /b/"


def test_endpoint_addresses_the_node_for_a_client_side_caller() -> None:
    assert ContainerHost.parse("10.0.0.7").endpoint(8081) == "http://10.0.0.7:8081"
    assert ContainerHost.parse("localhost").endpoint(8081) == "http://localhost:8081"


def test_describe_names_the_target() -> None:
    assert "local" in ContainerHost.parse("").describe()
    assert "ssh://node1" in ContainerHost.parse("node1").describe()


# ---------------------------------------------------------------------------
# ~ expansion
# ---------------------------------------------------------------------------


def test_tilde_expands_against_the_daemon_hosts_home() -> None:
    """The client's $HOME is the wrong user on a remote node."""
    assert (
        expand_remote_path("~/.llmdbench/nok8s", "/home/bench")
        == "/home/bench/.llmdbench/nok8s"
    )
    assert expand_remote_path("~", "/home/bench") == "/home/bench"


def test_absolute_paths_are_untouched() -> None:
    assert expand_remote_path("/srv/ws", "/home/bench") == "/srv/ws"


def test_unknown_home_leaves_the_tilde_for_the_remote_shell() -> None:
    assert expand_remote_path("~/x", "") == "~/x"


def test_a_username_after_the_tilde_is_not_rewritten() -> None:
    """`~other/x` names a different user's home; only a bare `~` is ours."""
    assert expand_remote_path("~other/x", "/home/bench") == "~other/x"
