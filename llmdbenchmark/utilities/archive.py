"""Compress a benchmark result set on the PVC, and read it back without expanding.

Generate -> compress -> copy: the pod produces every artifact, the set is compressed
in place, and the archive is what crosses the exec tunnel. Nothing is compressed or
expanded on the driver. Level 10 is the speed/size knee (see --compress-level in
README.md for the measured tradeoff).
"""

import posixpath
import re
import shlex
import signal
import subprocess
import sys
import tarfile
from pathlib import Path

DEFAULT_LEVEL = 10

# Left plain at any depth: results_store globs the reports and run_metadata.yaml off
# the live filesystem, and plots are already-compressed bytes. experiment-summary.yaml
# is a DoE run's index; it lives above any directory this runs in, so the entry only
# guards against that changing. Patterns must mean the same to ``find -name`` and to
# fnmatch/rglob, which disagree on character classes and '**' -- keep to plain '*'.
KEEP_PLAIN = (
    "benchmark_report*.yaml",
    "run_metadata.yaml",
    "experiment-summary.yaml",
    "*.png",
)

REMOTE_ARCHIVE_NAME = "workspace.tar.zst"

# THE INVARIANT every guard below serves: the set tar is told to pack must equal the
# set the delete loop removes. find writes the skip list, tar reads it and emits
# member names, the shell reads those back -- three quoting dialects, and every bug
# found here has been a mismatch between them, never a compression failure. Reason
# from that rule rather than the individual guards.
#
# A template, not concatenated f-strings: this deletes the only copy of a result set,
# so it has to read as shell to stay safely editable.
_REMOTE_SCRIPT = r"""
set -e
set -o pipefail
cd {quoted_dir}

# A crashed attempt can leave a corrupt archive; trust it only if it verifies.
if [ -f {archive} ] && zstd -t {archive} 2>/dev/null; then
    exit 0
fi
rm -f {archive}

# Ahead of mktemp, whose temp files would otherwise read as content.
if [ -z "$(find . -mindepth 1 -type f \( {keep_tests} \) -prune -o -print -quit)" ]; then
    exit 0
fi

# A newline splits one skip-list entry into two bogus patterns, so the keeper is
# archived instead of left plain. --null is not the fix: under it GNU tar honours
# only the list's first pattern, leaking every keeper after it.
#
# A backslash is refused for a subtler reason. `tar tf` escapes 161 of 255 byte
# values (measured), and an escaped name matches no file, so the loop skips it
# harmlessly -- unless a second real file's literal name equals that rendering, which
# the loop would then delete with its bytes in no archive. Every escape tar emits
# contains a backslash, so refusing backslashes makes that collision unreachable.
# Do not relax this without re-deriving that.
if find . -mindepth 1 \( -name '*
*' -o -name '*\\*' \) -print -quit | grep -q .; then
    echo 'refusing to compress: a file name contains a newline or backslash' >&2
    exit 3
fi

# -T0 reads the *host* core count, ignoring the pod's cgroup quota, and zstd's worker
# buffers scale with it. Ask the cgroup instead -- in-pod, so it cannot drift from the
# pod actually running. 0 means unlimited, where -T0 is right.
threads=$(
    q=; p=
    if [ -r /sys/fs/cgroup/cpu.max ]; then
        read -r q p < /sys/fs/cgroup/cpu.max
    elif [ -r /sys/fs/cgroup/cpu/cpu.cfs_quota_us ]; then
        q=$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)
        p=$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)
    fi
    # Both numeric and a positive period, or fall back to 0 (= all cores). An
    # unexpected cgroup format must not divide by zero: that aborts the whole
    # script here, leaving the set uncompressed with a clean exit.
    case "$q$p" in
        *[!0-9]* | '') echo 0 ;;
        *) if [ "$q" -gt 0 ] && [ "$p" -gt 0 ]; then
               echo $(( (q + p - 1) / p ))
           else
               echo 0
           fi ;;
    esac
)

# In the results dir, not /tmp: the pod's /tmp is small, the tar is multi-GB.
pack=$(mktemp ./.pack.XXXXXX.tar)
list=$(mktemp ./.list.XXXXXX)
skip=$(mktemp ./.list.XXXXXX)
trap 'rm -f -- "$pack" "$list" "$skip"' EXIT

# --no-wildcards, or --exclude-from reads a keeper holding '[' as a glob and
# archives it instead. Not --null: under it GNU tar honours only the list's *first*
# pattern, silently leaking every keeper after it.
find . -mindepth 1 -maxdepth 1 -type f \( {keep_tests} \
    -o -name '.pack.*' -o -name '.list.*' \) -printf './%f\n' > "$skip"
find . -mindepth 1 -type f \( {nested_finds} \) -print >> "$skip"

# --exclude={archive} as well as the skip list: the list is a snapshot taken above,
# so a concurrent run creating the archive between then and now would make it a member
# of its own pack -- and the delete loop, trusting the verified member list, would then
# remove the archive holding everything. The temp files need no live exclude; they exist
# before the snapshot.
tar cf "$pack" --anchored --no-wildcards --exclude='./{archive}' \
    --exclude-from="$skip" .
zstd -{level} -T"$threads" -q -f -o {archive} "$pack"
zstd -t {archive}

# The delete list, so a failure reading it must abort rather than delete nothing.
zstd -dc {archive} | tar tf - > "$list"
[ -s "$list" ]

# Individually, not `rm -rf` on the parent, which would take the excluded plots.
# IFS= : the default strips trailing space, resolving 'latency.png ' to the keeper
# 'latency.png' and deleting bytes held nowhere else.
while IFS= read -r member; do
    if [ -f "$member" ]; then
        rm -f -- "$member"
    fi
done < "$list"

find . -mindepth 1 -depth -type d -empty -delete

sed -e 's#^\./##' -e 's#/.*##' "$list" | sort -u | while IFS= read -r top; do
    # '..' as well as '.': a member named ../x would resolve $top to the parent,
    # which on a PVC holds every other result set. tar rooted at '.' cannot emit one,
    # so this only closes the gap if that ever stops being true.
    if [ -n "$top" ] && [ "$top" != '.' ] && [ "$top" != '..' ]; then
        # Exit status, not just output: a failing find prints nothing, and reading
        # that as "no keepers" deletes what was deliberately left out of the archive.
        # 2>/dev/null: the empty-dir sweep above already removed fully-archived
        # dirs, and one benign "No such file" per entry crowds the truncated warning
        # the caller logs. The exit status still gates the rm -rf.
        if keepers=$(find "./$top" \( {nested_finds} \) -print -quit 2>/dev/null); then
            if [ -z "$keepers" ]; then
                rm -rf -- "./$top"
            fi
        fi
    fi
done
"""


def remote_compress_script(remote_dir: str, level: int = DEFAULT_LEVEL) -> str:
    """Shell for compressing a PVC results dir in place, run via ``kube_exec``.

    Deletion is gated on ``zstd -t`` *and* a readable member list: the PVC holds the
    only copy. Must go to ``kube_exec`` (argv), never ``kube``, which joins arguments
    into a string ``bash -c`` re-parses locally -- pointing the deletes at the
    driver's own filesystem. ``bash`` not ``sh``: dash rejects ``pipefail``.
    """
    # Built by find, not --exclude globs: a glob also matches directories, so a dir
    # named 'logs.txt' would leave the archive while the delete loop still removed it.
    keep_tests = " -o ".join(
        f"-name '{pattern}'" for pattern in (*KEEP_PLAIN, REMOTE_ARCHIVE_NAME)
    )
    # nested_finds omits the archive itself: it only ever exists at depth 1.
    nested_finds = " -o ".join(f"-name '{pattern}'" for pattern in KEEP_PLAIN)
    return _REMOTE_SCRIPT.format(
        quoted_dir=shlex.quote(remote_dir),
        archive=REMOTE_ARCHIVE_NAME,
        keep_tests=keep_tests,
        nested_finds=nested_finds,
        level=level,
    )


_PRESENT = "__LLMDBENCH_PRESENT__"
_ABSENT = "__LLMDBENCH_ABSENT__"


class RemoteReadError(Exception):
    """Not a ``RuntimeError``, or ``_UNREADABLE`` would catch it and degrade a
    dropped tunnel to the "file absent" this exists to prevent."""


def _local_zstd_source(archive: Path) -> subprocess.Popen:
    return subprocess.Popen(
        ["zstd", "-dc", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _read_archive(archive, consume, partial: bool = False, source=None) -> None:
    """Open ``archive`` for reading and hand the tar to ``consume``.

    Set ``partial`` when ``consume`` may return before the last member. ``source``
    overrides where the bytes come from, so a remote archive reuses the tar/link
    handling below rather than reimplementing it.
    """
    proc = (source or _local_zstd_source)(archive)
    upstream = getattr(proc, "upstream", None)
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            consume(tar)
    finally:
        # Close first, or zstd blocks writing into a pipe nobody drains and
        # wait() deadlocks. An early-stopping consumer makes that the norm.
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        code = proc.wait()
        # Before zstd's verdict: it reports the truncation, not the cause.
        if upstream is not None:
            up_stderr = upstream.stderr.read().decode("utf-8", errors="replace")
            upstream.stderr.close()
            up_code = upstream.wait()
            if up_code != 0 and not (partial and up_code == -signal.SIGPIPE):
                raise RemoteReadError(
                    f"reading {archive} failed (exit={up_code}): {up_stderr[:300]}"
                )
        # Only SIGPIPE is forgiven -- and it cannot be told apart from a truncation
        # the consumer stopped before reaching. Deletion is gated on `zstd -t`, so a
        # bad archive reaching here is already rare.
        if code != 0 and not (partial and code == -signal.SIGPIPE):
            raise RuntimeError(f"zstd -dc exited {code}: {stderr}")


def read_member(root: Path, name: str) -> bytes | None:
    """Return ``name``'s bytes from under ``root``, plain or from an archive.

    Reads either tree shape, so a compressed result set need not be expanded to
    disk to be read once. ``None`` when the file is in neither place.
    """
    root = Path(root)
    plain = root / name
    if plain.is_file():
        return plain.read_bytes()

    for archive, prefix in _archives_covering(root):
        payload = _read_one(archive, f"{prefix}{name}")
        if payload is not None:
            return payload
    return None


def read_members(root: Path, pattern: str) -> dict[str, bytes]:
    """Return ``{relative path: bytes}`` for every match of ``pattern`` under ``root``.

    The globbing counterpart to :func:`read_member`. Plain files win, and an archive
    is consulted only when the tree holds none, so a partially-expanded tree is
    never merged with its own archive.
    """
    root = Path(root)
    plain = {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.glob(pattern))
        if p.is_file()
    }
    if plain:
        return plain

    for archive, prefix in _archives_covering(root):
        found = _read_matching(archive, prefix, pattern)
        if found is None:
            continue
        if found:
            return found
    return {}


def _read_matching(
    archive, prefix: str, pattern: str, source=None
) -> dict[str, bytes] | None:
    """``None`` when the archive could not be read -- not the same as no match."""
    found: dict[str, bytes] = {}

    def _consume(tar: tarfile.TarFile) -> None:
        for member in tar:
            if not member.isfile():
                continue
            name = _strip_dot_slash(member.name)
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix) :]
            if not _glob_match(relative, pattern):
                continue
            handle = tar.extractfile(member)
            if handle is not None:
                found[relative] = handle.read()

    try:
        # Not partial: every member has to be examined, so the reader is
        # drained rather than stopped at the first hit.
        _read_archive(archive, _consume, source=source)
    except _UNREADABLE as exc:
        _warn_unreadable(archive, exc)
        return None
    return found


def _glob_match(relative: str, pattern: str) -> bool:
    """Match ``pattern`` the way ``Path.glob`` does, not the way ``fnmatch`` does.

    fnmatch's ``*`` crosses ``/``, so it would fold a nested file into a match the
    plain-filesystem side excludes -- the same glob answering differently depending
    on whether the tree happens to be compressed. (``PurePath.full_match`` is 3.13+;
    the floor here is 3.11.)
    """
    segments = pattern.split("**/")
    expr = "(?:[^/]+/)*".join(
        "[^/]*".join(re.escape(part) for part in segment.split("*"))
        for segment in segments
    )
    return re.fullmatch(expr, relative) is not None


def _strip_dot_slash(name: str) -> str:
    """Drop a leading ``./`` -- as a prefix, not as ``lstrip('./')``, which is a
    character set and would eat the leading dot of a real name."""
    return name[2:] if name.startswith("./") else name


def _archives_covering(root: Path) -> list[tuple[Path, str]]:
    """Archives that may hold ``root``'s contents, with each one's member prefix.

    Only inside ``root``: searching wider would read a sibling tree's archive as this
    one's.
    """
    archive = root / REMOTE_ARCHIVE_NAME
    return [(archive, "")] if archive.is_file() else []


_UNREADABLE = (RuntimeError, tarfile.TarError, OSError)


def _warn_unreadable(archive: Path, exc: BaseException) -> None:
    """Report an unreadable archive rather than letting it read as "member absent".

    The originals are gone once it verifies, so silence looks like an empty run.
    """
    print(
        f"WARNING: cannot read {archive} ({type(exc).__name__}: {exc}) -- "
        f"treating its contents as absent",
        file=sys.stderr,
    )


def _read_one(archive, wanted: str, _depth: int = 0, source=None) -> bytes | None:
    """First member of ``archive`` named exactly ``wanted``, or None.

    A link member carries no data (tar stores the payload once), so it is resolved
    by re-reading its target -- else one name of a linked pair reads as absent.
    """
    found: list[bytes] = []
    link_to: list[str] = []

    def _consume(tar: tarfile.TarFile) -> None:
        for member in tar:
            if _strip_dot_slash(member.name) != wanted:
                continue
            if member.isfile():
                handle = tar.extractfile(member)
                if handle is not None:
                    found.append(handle.read())
            elif member.islnk() or member.issym():
                target = _strip_dot_slash(member.linkname)
                if member.issym() and not target.startswith("/"):
                    target = posixpath.normpath(
                        posixpath.join(posixpath.dirname(wanted), target)
                    )
                link_to.append(target)
            return

    try:
        _read_archive(archive, _consume, partial=True, source=source)
    except _UNREADABLE as exc:
        _warn_unreadable(archive, exc)
        return None
    if found:
        return found[0]
    # Depth cap, not a visited set: a tar can hold a link cycle, and one hop is
    # all a real archive needs.
    if link_to and _depth < 4:
        return _read_one(archive, link_to[0], _depth + 1, source=source)
    return None


# Reading a result set left on the PVC (--data-collect results/skip)


class RemoteReader:
    # Absence goes through `test -f` rather than a failed `cat`: kubectl's exit code
    # is not always the remote command's, so a missing file and a broken tunnel both
    # surface as 1.

    def __init__(self, cmd, pod: str, namespace: str, source=None):
        self.cmd = cmd
        self.pod = pod
        self.namespace = namespace
        # Injectable so the reader is testable without a cluster.
        self._source = source or self._exec_source

    def _exec_argv(self, *remote_argv: str) -> list[str]:
        return [
            self.cmd._kube_bin,
            *self.cmd._kubeconfig_args(),
            "--namespace",
            self.namespace,
            "exec",
            self.pod,
            "--",
            *remote_argv,
        ]

    def exists(self, path: str) -> bool:
        # Sentinel on stdout, not an exit code: kubectl returns 1 for a missing pod,
        # an unreachable apiserver and a forbidden exec too.
        result = self.cmd.kube_exec(
            self.pod,
            "sh",
            "-c",
            f"test -f {shlex.quote(path)} && echo {_PRESENT} || echo {_ABSENT}",
            namespace=self.namespace,
            check=False,
        )
        verdict = (result.stdout or "").strip().splitlines()
        if verdict:
            if verdict[-1] == _PRESENT:
                return True
            if verdict[-1] == _ABSENT:
                return False
        raise RemoteReadError(
            f"cannot test {self.pod}:{path} (exit={result.exit_code}): "
            f"{(result.stderr or result.stdout)[:300]}"
        )

    def cat(self, path: str) -> bytes:
        # Raw Popen, not kube_exec: that runs text=True and would decode archive
        # bytes as UTF-8.
        argv = self._exec_argv("cat", path)
        try:
            proc = subprocess.run(argv, capture_output=True, check=False)
        except OSError as exc:
            raise RemoteReadError(f"cannot read {self.pod}:{path}: {exc}") from exc
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            raise RemoteReadError(
                f"cannot read {self.pod}:{path} (exit={proc.returncode}): "
                f"{stderr[:300]}"
            )
        return proc.stdout

    def _exec_source(self, path) -> subprocess.Popen:
        cat = subprocess.Popen(
            self._exec_argv("cat", str(path)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        zstd = subprocess.Popen(
            ["zstd", "-dc"],
            stdin=cat.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Ours is closed so zstd owns the only read end and cat sees EOF/SIGPIPE.
        cat.stdout.close()
        # Checked by _read_archive; unwatched it would read as a short archive.
        zstd.upstream = cat
        return zstd

    def archive_source(self):
        return self._source


def read_member_remote(reader: RemoteReader, root: str, name: str) -> bytes | None:
    plain = posixpath.join(root, name)
    if reader.exists(plain):
        return reader.cat(plain)

    archive = posixpath.join(root, REMOTE_ARCHIVE_NAME)
    if not reader.exists(archive):
        return None
    return _read_one(archive, name, source=reader.archive_source())


def read_members_remote(
    reader: RemoteReader, root: str, pattern: str
) -> dict[str, bytes]:
    # find lists rather than Path.glob, which cannot walk a pod; _glob_match still
    # decides, so one glob dialect spans both transports.
    plain = {
        relative: reader.cat(posixpath.join(root, relative))
        for relative in _remote_relative_files(reader, root)
        if _glob_match(relative, pattern)
    }
    if plain:
        return plain

    archive = posixpath.join(root, REMOTE_ARCHIVE_NAME)
    if not reader.exists(archive):
        return {}
    found = _read_matching(archive, "", pattern, source=reader.archive_source())
    return found or {}


def _remote_relative_files(reader: RemoteReader, root: str) -> list[str]:
    result = reader.cmd.kube_exec(
        reader.pod,
        "find",
        root,
        "-type",
        "f",
        namespace=reader.namespace,
        check=False,
    )
    if not result.success:
        raise RemoteReadError(
            f"cannot list {reader.pod}:{root} (exit={result.exit_code}): "
            f"{(result.stderr or result.stdout)[:300]}"
        )
    prefix = root.rstrip("/") + "/"
    return [
        line[len(prefix) :]
        for line in (raw.strip() for raw in result.stdout.splitlines())
        if line.startswith(prefix)
    ]
