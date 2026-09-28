"""The preprocess script's handling of a pinned KV-transfer device.

These run the real script against stubbed `ip`, `ibstat` and `show_gids.sh`, so
they cover the two ways a pod ends up unable to reach its peer: an explicit pin
being overwritten by device discovery, and discovery finding nothing on a node
whose rails share one subnet (which used to drop the source-based routing rules
along with the device list).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    PROJECT_ROOT
    / "llmdbenchmark"
    / "standup"
    / "preprocess"
    / "set_llmdbench_environment.py"
)

# Two rails, both inside 172.23.0.0/16, which is what a multi-NIC
# NetworkAttachmentDefinition produces and what forces source-based routing.
_SAME_SUBNET_ADDRS = """\
1: lo    inet 127.0.0.1/8 scope host lo
1: lo    inet6 ::1/128 scope host
2: eth0    inet 10.0.0.5/23 scope global eth0
2: eth0    inet6 fe80::aaaa/64 scope link
3: net1-0    inet 172.23.0.5/16 scope global net1-0
3: net1-0    inet6 fe80::1234/64 scope link
4: net1-1    inet 172.23.1.5/16 scope global net1-1
4: net1-1    inet6 fe80::5678/64 scope link
"""

_SAME_SUBNET_ROUTES = """\
default via 10.0.0.1 dev eth0
172.23.0.0/16 dev net1-0 proto kernel scope link src 172.23.0.5
172.23.0.0/16 dev net1-1 proto kernel scope link src 172.23.1.5
"""

# One rail on its own subnet: no routing tables needed, so this isolates the
# device-pinning behaviour.
_ONE_RAIL_ADDRS = """\
1: lo    inet 127.0.0.1/8 scope host lo
1: lo    inet6 ::1/128 scope host
2: eth0    inet 10.0.0.5/23 scope global eth0
2: eth0    inet6 fe80::aaaa/64 scope link
3: net1-0    inet 172.23.0.5/16 scope global net1-0
3: net1-0    inet6 fe80::1234/64 scope link
"""

_ONE_RAIL_ROUTES = """\
default via 10.0.0.1 dev eth0
172.23.0.0/16 dev net1-0 proto kernel scope link src 172.23.0.5
"""

_IBSTAT = """\
CA 'mlx5_1'
\tCA type: MT4125
\tNumber of ports: 1
\tPort 1:
\t\tState: Active
\t\tNode GUID: 0x0000000000001234
"""

# Tab-separated, and the script skips the two header lines.
_SHOW_GIDS_MATCHING = """\
DEV\tPORT\tINDEX\tGID\tIPv4\tVER\tDEV
---\t----\t-----\t---\t----\t---\t---
mlx5_1\t1\t2\tfe80:0000:0000:0000\t172.23.0.5\tv2\tnet1-0
"""

# mlx5_1 sits at GID index 8/9 while the more numerous mlx5_0 sits at 6/7, so
# the selected index set never matches mlx5_1 -- the real shape seen on a node
# that does not expose RoCE GIDs at uniform indexes.
_SHOW_GIDS_MISMATCHED = """\
DEV\tPORT\tINDEX\tGID\tIPv4\tVER\tDEV
---\t----\t-----\t---\t----\t---\t---
mlx5_0\t1\t6\tfe80:0000:0000:0000\t10.0.0.5\tv2\teth0
mlx5_0\t1\t7\tfe80:0000:0000:0001\t10.0.0.5\tv2\teth0
mlx5_1\t1\t8\tfe80:0000:0000:0002\t172.23.0.5\tv2\tnet1-0
mlx5_1\t1\t9\tfe80:0000:0000:0003\t172.23.0.5\tv2\tnet1-0
"""


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _run(
    tmp_path: Path,
    addrs: str,
    routes: str,
    show_gids: str,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Run the script in initContainer mode; return (stdout, env file text)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "addrs").write_text(addrs)
    (tmp_path / "routes").write_text(routes)
    _stub(
        bin_dir / "ip",
        f'case "$*" in\n'
        f'  "-o address list") cat {tmp_path / "addrs"} ;;\n'
        f'  "route list") cat {tmp_path / "routes"} ;;\n'
        f"  *) exit 0 ;;\nesac",
    )
    (tmp_path / "ibstat.out").write_text(_IBSTAT)
    (tmp_path / "gids.out").write_text(show_gids)
    _stub(bin_dir / "ibstat", f"cat {tmp_path / 'ibstat.out'}")
    _stub(bin_dir / "show_gids.sh", f"cat {tmp_path / 'gids.out'}")
    _stub(bin_dir / "gemini-arp-fix.sh", "exit 0")

    conf_dir = tmp_path / "iproute2"
    conf_dir.mkdir()
    (conf_dir / "rt_tables").write_text("255 local\n254 main\n")

    home = tmp_path / "home"
    home.mkdir()
    env_file = tmp_path / "shared-config" / "llmdbench_env.sh"
    env_file.parent.mkdir()

    full_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "IPROUTE2_CONF_DIR": str(conf_dir),
    }
    full_env.update(env or {})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "-e", str(env_file), "-i"],
        capture_output=True,
        text=True,
        env=full_env,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout, env_file.read_text()


class TestPinnedDeviceIsPreserved:
    def test_discovery_sets_the_device_when_nothing_is_pinned(self, tmp_path):
        stdout, env_file = _run(
            tmp_path, _ONE_RAIL_ADDRS, _ONE_RAIL_ROUTES, _SHOW_GIDS_MATCHING
        )

        assert "INFO: HCA IDs selected: ['mlx5_1']" in stdout
        assert 'export UCX_NET_DEVICES="net1-0"' in env_file

    def test_a_pinned_device_survives_discovery(self, tmp_path):
        """The env file is sourced by the vLLM command, so an export here would
        silently win over the container's own UCX_NET_DEVICES."""
        stdout, env_file = _run(
            tmp_path,
            _ONE_RAIL_ADDRS,
            _ONE_RAIL_ROUTES,
            _SHOW_GIDS_MATCHING,
            env={"UCX_NET_DEVICES": "eth0"},
        )

        assert "export UCX_NET_DEVICES" not in env_file
        assert 'UCX_NET_DEVICES is already set to "eth0"' in stdout
        # Unrelated discovery output is still emitted.
        assert 'export NCCL_IB_HCA="=mlx5_1"' in env_file

    def test_omitting_the_variable_still_wins_over_a_pin(self, tmp_path):
        _, env_file = _run(
            tmp_path,
            _ONE_RAIL_ADDRS,
            _ONE_RAIL_ROUTES,
            _SHOW_GIDS_MATCHING,
            env={"UCX_NET_DEVICES": "eth0"},
        )
        assert "export UCX_NET_DEVICES" not in env_file


class TestRoutingSurvivesEmptyDiscovery:
    def test_same_subnet_rails_get_routing_rules_without_any_usable_hca(self, tmp_path):
        stdout, env_file = _run(
            tmp_path, _SAME_SUBNET_ADDRS, _SAME_SUBNET_ROUTES, _SHOW_GIDS_MISMATCHED
        )

        assert "INFO: HCA IDs selected: []" in stdout
        # No device list to pin, but the rails are still unusable without these.
        assert "ip rule add from 172.23.0.5 lookup table0" in env_file
        assert "ip rule add from 172.23.1.5 lookup table1" in env_file
        assert "ip route add 172.23.0.0/16 dev net1-0 src 172.23.0.5 table table0" in (
            env_file
        )
        assert "export UCX_NET_DEVICES" not in env_file

    def test_empty_discovery_on_a_shared_subnet_is_reported(self, tmp_path):
        stdout, _ = _run(
            tmp_path, _SAME_SUBNET_ADDRS, _SAME_SUBNET_ROUTES, _SHOW_GIDS_MISMATCHED
        )

        assert "WARNING: no usable HCA was found" in stdout
        assert "vllmCommon.ucxNetDevices" in stdout
        assert "do not include all of the selected" in stdout

    def test_routing_rules_are_emitted_when_discovery_succeeds(self, tmp_path):
        _, env_file = _run(
            tmp_path, _SAME_SUBNET_ADDRS, _SAME_SUBNET_ROUTES, _SHOW_GIDS_MATCHING
        )

        assert "ip rule add from 172.23.0.5 lookup table0" in env_file
        assert 'export UCX_NET_DEVICES="net1-0"' in env_file


def test_missing_rt_tables_directory_is_reported_not_fatal(tmp_path):
    """Neither directory existing used to raise instead of warning."""
    # Same lookup order as the script once IPROUTE2_CONF_DIR misses.
    system_rt_tables = next(
        (
            p
            for p in (
                Path(d, "rt_tables") for d in ("/etc/iproute2", "/usr/share/iproute2")
            )
            if p.is_file()
        ),
        None,
    )
    if system_rt_tables and not os.access(system_rt_tables, os.W_OK):
        # The script appends to it (it runs as root in the pod), so a
        # read-only system file cannot exercise either branch here.
        pytest.skip(f"{system_rt_tables} exists but is not writable")

    stdout, env_file = _run(
        tmp_path,
        _SAME_SUBNET_ADDRS,
        _SAME_SUBNET_ROUTES,
        _SHOW_GIDS_MATCHING,
        env={"IPROUTE2_CONF_DIR": str(tmp_path / "absent")},
    )

    assert env_file.startswith("#!/usr/bin/env bash")
    if system_rt_tables:
        # A system rt_tables was picked up instead, so routing still happens.
        assert "ip rule add from 172.23.0.5" in env_file
    else:
        assert 'unable to find a directory for the file "rt_tables"' in stdout
        assert "ip rule add" not in env_file


def test_gid_superset_accepts_non_uniform_tables() -> None:
    """A rail with extra GID indexes still qualifies.

    Exact equality rejected every device on nodes whose RoCE GID table is
    non-uniform, which left the pod with no UCX_NET_DEVICES, no NCCL_IB_HCA and
    no source-based routing rules.
    """
    s_gid = ["3", "4"]
    hcadev_to_gid = {
        "mlx5_1": ["3", "4"],
        "mlx5_3": ["3", "4", "5"],  # extra index -- previously rejected
        "mlx5_5": ["4"],  # missing one of s_gid -- still rejected
        "mlx5_7": [],  # no GID data -- still rejected
    }

    def qualifies(hcaid: str) -> bool:
        device_gids = hcadev_to_gid.get(hcaid, [])
        return bool(device_gids) and set(s_gid).issubset(device_gids)

    assert qualifies("mlx5_1")
    assert qualifies("mlx5_3")
    assert not qualifies("mlx5_5")
    assert not qualifies("mlx5_7")


def test_gid_check_source_uses_subset_not_equality() -> None:
    """Guard the actual script, not just a restatement of the rule."""
    import pathlib

    src = pathlib.Path(
        "llmdbenchmark/standup/preprocess/set_llmdbench_environment.py"
    ).read_text()
    assert "set(s_gid).issubset(device_gids)" in src
    assert "if s_gid == hcadev_to_gid[hcaid]:" not in src
