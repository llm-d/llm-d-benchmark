"""Container-runtime connection target for the no-Kubernetes (nok8s) method.

``nok8s.connection`` names the host whose docker/podman runs the stack.  The
default ``localhost`` keeps every command exactly as it was before this module
existed; an ``ssh://`` value points the runtime client at a remote daemon over
SSH, so ``llmdbenchmark standup`` can bring up vLLM + EPP + Envoy on a
bare-metal node without anybody logging into it.

Both runtimes speak SSH natively, so no hand-rolled ``ssh -L`` tunnel is
needed for the control plane::

    docker -H ssh://user@node run ...
    podman --url ssh://user@node/run/user/1000/podman/podman.sock run ...

What SSH does *not* solve is that a nok8s standup is more than ``run``: it
stages EPP/Envoy config files and bind-mounts them, expands ``~``, and probes
readiness over HTTP.  Every one of those resolves on the machine that holds
the files or the socket, which is the *daemon* host, not the client.  So this
class exposes three things a step needs, rather than only a connection flag:

* :meth:`runtime_args` -- the client flag that reaches the daemon.
* :meth:`shell` -- run a command *on* the daemon host (``mkdir``, ``curl``,
  ``nvidia-smi``), so probes describe the host that will actually serve.
* :meth:`push_dir` / :meth:`pull_dir` -- move bind-mount sources and results
  between client and daemon host.

``tcp://`` is deliberately unsupported: an unencrypted, unauthenticated docker
socket is root on the node for anyone who can reach the port, and the SSH
transport removes any reason to open one.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from urllib.parse import urlparse

LOCAL = "localhost"

# Default rootful socket paths, used when an ssh:// URL names no path. docker
# has one well-known socket; podman resolves its own (rootless sockets live
# under /run/user/<uid>), so we leave podman's path empty and let the client
# ask the service.
_DEFAULT_SOCKETS = {"docker": "/var/run/docker.sock"}

# Non-interactive by default: a standup that blocks on a passphrase prompt or a
# host-key question looks like a hang. Callers who want strict host-key
# checking can add it back through nok8s.sshArgs.
_DEFAULT_SSH_ARGS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")


class ContainerHostError(ValueError):
    """A ``nok8s.connection`` value that cannot be used as written."""


@dataclass(frozen=True)
class ContainerHost:
    """Where a nok8s stack's containers run.

    Construct with :meth:`parse`; the fields are the parsed form of
    ``nok8s.connection`` plus the SSH options that reach it.
    """

    runtime: str = "docker"
    user: str = ""
    host: str = LOCAL
    port: int | None = None
    socket: str = ""
    identity: str = ""
    ssh_args: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------ #
    # construction
    # ------------------------------------------------------------------ #
    @classmethod
    def parse(
        cls,
        connection: str | None,
        runtime: str = "docker",
        identity: str = "",
        ssh_args: list[str] | tuple[str, ...] | None = None,
    ) -> "ContainerHost":
        """Build a host from a ``nok8s.connection`` value.

        Accepted forms:

        * ``""`` / ``localhost`` / ``local`` / ``unix://...`` -- the local
          runtime, i.e. exactly the pre-existing behaviour.
        * ``ssh://[user@]host[:port][/socket-path]``
        * ``[user@]host`` -- bare hostname or IP, treated as ``ssh://``, so a
          scenario can say ``connection: 10.0.0.7`` and mean the obvious
          thing. This is the form the "just give it the node's IP" case wants.

        Raises:
            ContainerHostError: for ``tcp://`` (unauthenticated), any other
                scheme, or a value with no host part.
        """
        runtime = (runtime or "docker").strip() or "docker"
        raw = (connection or "").strip()
        args = tuple(ssh_args or ()) or _DEFAULT_SSH_ARGS

        if raw.lower() in ("", LOCAL, "local", "127.0.0.1", "::1"):
            return cls(runtime=runtime)
        if raw.startswith("unix://") or raw.startswith("/"):
            # A local socket path is still local; keep it as the plain local
            # runtime rather than inventing a flag for the default socket.
            return cls(runtime=runtime)

        scheme, sep, rest = raw.partition("://")
        if not sep:
            # Bare "[user@]host[:port]" -- imply ssh. Keyed on the separator,
            # not on `rest`: "ssh://" has a scheme and an empty host, and
            # treating it as a bare hostname would resolve it to a node
            # literally called "ssh" instead of reporting the empty host.
            scheme, rest = "ssh", raw
        scheme = scheme.lower()

        if scheme == "tcp":
            raise ContainerHostError(
                "nok8s.connection 'tcp://...' is not supported: an unencrypted "
                "docker/podman TCP socket grants root on that node to anyone "
                "who can reach the port. Use ssh:// instead "
                "(e.g. ssh://user@10.0.0.7), which needs no daemon "
                "reconfiguration."
            )
        if scheme not in ("ssh",):
            raise ContainerHostError(
                f"nok8s.connection scheme '{scheme}://' is not supported. Use "
                f"'{LOCAL}' for the local runtime or 'ssh://[user@]host[:port]"
                f"[/socket]' for a remote one."
            )

        parsed = urlparse(f"ssh://{rest}")
        if not parsed.hostname:
            raise ContainerHostError(
                f"nok8s.connection '{connection}' has no host part. Expected "
                f"'ssh://[user@]host[:port][/socket]'."
            )
        try:
            port = parsed.port
        except ValueError as exc:
            # urlparse defers port validation to attribute access.
            raise ContainerHostError(
                f"nok8s.connection '{connection}' has an invalid port: {exc}"
            ) from exc

        return cls(
            runtime=runtime,
            user=parsed.username or "",
            host=parsed.hostname,
            port=port,
            socket=(parsed.path or "").rstrip("/"),
            identity=(identity or "").strip(),
            ssh_args=args,
        )

    # ------------------------------------------------------------------ #
    # properties
    # ------------------------------------------------------------------ #
    @property
    def is_remote(self) -> bool:
        """True when the containers run on another machine."""
        return self.host != LOCAL

    @property
    def destination(self) -> str:
        """``[user@]host`` for an ``ssh``/``scp`` command line."""
        return f"{self.user}@{self.host}" if self.user else self.host

    @property
    def url(self) -> str:
        """The ``ssh://`` URL handed to the runtime client."""
        if not self.is_remote:
            return ""
        socket = self.socket or _DEFAULT_SOCKETS.get(self.runtime, "")
        port = f":{self.port}" if self.port else ""
        return f"ssh://{self.destination}{port}{socket}"

    def describe(self) -> str:
        """Short human-readable form for logs and step messages."""
        return (
            f"{self.runtime} @ {self.url}"
            if self.is_remote
            else f"{self.runtime} (local)"
        )

    # ------------------------------------------------------------------ #
    # command construction
    # ------------------------------------------------------------------ #
    def runtime_args(self) -> str:
        """Client flags that point the runtime at this host (``""`` if local).

        docker takes ``-H``; podman takes ``--url`` and, for a non-default key,
        ``--identity``. Both must come *before* the subcommand, which is why
        callers build ``f"{runtime} {args} run ..."`` rather than appending.
        """
        if not self.is_remote:
            return ""
        parts: list[str] = []
        if self.runtime == "podman":
            parts += ["--url", shlex.quote(self.url)]
            if self.identity:
                parts += ["--identity", shlex.quote(self.identity)]
        else:
            parts += ["-H", shlex.quote(self.url)]
        return " ".join(parts)

    def runtime_cmd(self, *args: str) -> str:
        """A full runtime command line with the connection flags injected."""
        flags = self.runtime_args()
        head = f"{self.runtime} {flags}" if flags else self.runtime
        return " ".join([head, *args])

    def runtime_env(self) -> str:
        """``VAR=value`` prefix for tools that read the connection from env.

        docker's ``-H`` does not reach a helper that shells out to the client
        itself, and podman's ``--identity`` only applies to podman; exporting
        ``DOCKER_HOST`` / ``CONTAINER_HOST`` covers those. Empty when local.
        """
        if not self.is_remote:
            return ""
        if self.runtime == "podman":
            env = [f"CONTAINER_HOST={shlex.quote(self.url)}"]
            if self.identity:
                env.append(f"CONTAINER_SSHKEY={shlex.quote(self.identity)}")
            return " ".join(env)
        return f"DOCKER_HOST={shlex.quote(self.url)}"

    def _ssh_prefix(self, binary: str = "ssh") -> list[str]:
        """``ssh``/``scp`` invocation with identity, port and options applied."""
        parts = [binary, *self.ssh_args]
        if self.identity:
            parts += ["-i", shlex.quote(self.identity)]
        if self.port:
            # scp spells the port -P; ssh spells it -p.
            parts += ["-P" if binary == "scp" else "-p", str(self.port)]
        return parts

    def shell(self, command: str) -> str:
        """Wrap *command* so it runs on the daemon host.

        Returns *command* unchanged when local, so a caller can use this
        unconditionally and the local path stays byte-identical to before.
        """
        if not self.is_remote:
            return command
        return " ".join([*self._ssh_prefix(), self.destination, shlex.quote(command)])

    def push_dir(self, local_dir: str, remote_dir: str) -> str:
        """Command that mirrors *local_dir*'s contents into *remote_dir*.

        Bind-mount sources have to exist on the daemon host, so staged EPP and
        Envoy configs are copied there before any container starts. ``scp -r``
        rather than ``rsync`` because ``rsync`` is not reliably installed on a
        minimal compute node, while ``scp`` ships with the SSH the connection
        already depends on.

        The trailing ``/.`` copies the directory *contents*, so a re-run
        overwrites the staged files instead of nesting a second copy inside.
        """
        if not self.is_remote:
            return f"cp -a {shlex.quote(local_dir)}/. {shlex.quote(remote_dir)}/"
        scp = " ".join(self._ssh_prefix("scp"))
        return (
            f"{self.shell(f'mkdir -p {shlex.quote(remote_dir)}')} && "
            f"{scp} -r {shlex.quote(local_dir)}/. "
            f"{self.destination}:{shlex.quote(remote_dir)}/"
        )

    def push_file(self, local_file: str, remote_file: str) -> str:
        """Command that copies one file to *remote_file* on the daemon host."""
        if not self.is_remote:
            return f"cp -a {shlex.quote(local_file)} {shlex.quote(remote_file)}"
        scp = " ".join(self._ssh_prefix("scp"))
        parent = remote_file.rsplit("/", 1)[0] or "/"
        return (
            f"{self.shell(f'mkdir -p {shlex.quote(parent)}')} && "
            f"{scp} {shlex.quote(local_file)} "
            f"{self.destination}:{shlex.quote(remote_file)}"
        )

    def pull_dir(self, remote_dir: str, local_dir: str) -> str:
        """Command that copies *remote_dir*'s contents down to *local_dir*."""
        if not self.is_remote:
            return f"cp -a {shlex.quote(remote_dir)}/. {shlex.quote(local_dir)}/"
        scp = " ".join(self._ssh_prefix("scp"))
        return (
            f"mkdir -p {shlex.quote(local_dir)} && {scp} -r "
            f"{self.destination}:{shlex.quote(remote_dir)}/. "
            f"{shlex.quote(local_dir)}/"
        )

    def endpoint(self, port: int, scheme: str = "http") -> str:
        """URL a *client-side* caller uses to reach *port* on the daemon host.

        The smoketest and any manual ``curl`` run on the client, so for a
        remote stack they must address the node, not ``localhost``. Steps that
        run *inside* the node (the harness container, with ``--network host``)
        keep using ``localhost`` -- see ``clientEndpoint`` vs ``endpoint`` in
        ``34_nok8s-containers.yaml``.
        """
        return f"{scheme}://{self.host}:{port}"


def expand_remote_path(path: str, home: str = "") -> str:
    """Expand a leading ``~`` against the *daemon host's* home directory.

    ``os.path.expanduser`` resolves against the client's ``$HOME``, which is
    the wrong user on a remote node (and wrong even locally under ``sudo``).
    Paths are expanded from the home directory read off the daemon host; with
    no *home* known the ``~`` is left in place for the remote shell to expand,
    which is correct for ``shell()`` but not for a bind-mount source, so
    callers pass the value they read from the host.
    """
    if not path.startswith("~"):
        return path
    if not home:
        return path
    return re.sub(r"^~(?=/|$)", home.rstrip("/"), path)
