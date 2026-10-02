"""The /dev/shm volume must be able to hold the CPU KV-offload region.

vLLM's OffloadingConnector mmaps `cpu_bytes_to_use` inside /dev/shm. When the
emptyDir sizeLimit is smaller, the region cannot be created and the decode pod
crashloops with FileExistsError on a partially-created .mmap -- an error that
names neither knob. UCX/NIXL also keep their own segments in the same tmpfs,
so sizing /dev/shm exactly to the offload region leaves no room for them;
require at least a 2Gi buffer on top.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = sorted((PROJECT_ROOT / "config" / "scenarios").rglob("*.yaml"))
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "kv_offload_shm.yaml"

_UNITS = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}
_MIN_BUFFER_BYTES = 2 * 1024**3


def _to_bytes(value) -> int | None:
    """Parse a Kubernetes quantity like '512Gi' into bytes."""
    if isinstance(value, int):
        return value
    text = str(value).strip()
    for suffix, factor in _UNITS.items():
        if text.endswith(suffix):
            try:
                return int(float(text[: -len(suffix)]) * factor)
            except ValueError:
                return None
    try:
        return int(text)
    except ValueError:
        return None


def _offload_stacks(paths):
    """Yield (scenario, stack name, offload bytes, shm bytes) per offloading stack."""
    for path in paths:
        try:
            doc = yaml.full_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(doc, dict):
            continue
        stacks = doc.get("scenario") or []
        shared = doc.get("shared") or {}
        if not isinstance(stacks, list):
            continue
        for stack in stacks:
            if not isinstance(stack, dict):
                continue
            merged = {**shared, **stack}
            # vllmCommon sits either at stack level or inside one of the
            # author-facing sections (issue #1064).
            blocks = []
            for holder in (
                merged,
                merged.get("common") or {},
                merged.get("modelservice") or {},
                merged.get("standalone") or {},
            ):
                if isinstance(holder, dict) and isinstance(
                    holder.get("vllmCommon"), dict
                ):
                    blocks.append(holder["vllmCommon"])
            if not blocks:
                continue

            offload = None
            shm = None
            for common in blocks:
                extra = ((common.get("kvTransfer") or {}).get("extraConfig")) or {}
                if isinstance(extra, dict) and extra.get("cpu_bytes_to_use"):
                    offload = extra["cpu_bytes_to_use"]
                for volume in common.get("volumes") or []:
                    if not isinstance(volume, dict):
                        continue
                    empty_dir = volume.get("emptyDir") or {}
                    if volume.get("name") == "dshm" and isinstance(empty_dir, dict):
                        shm = empty_dir.get("sizeLimit")
            if not offload:
                continue
            yield (
                path.relative_to(PROJECT_ROOT),
                stack.get("name", "?"),
                _to_bytes(offload),
                _to_bytes(shm) if shm is not None else None,
            )


def _undersized(stacks):
    return [
        f"{scenario}:{stack} offloads {offload / 1024**3:g}GiB but caps /dev/shm "
        f"at {shm / 1024**3:g}GiB (need >= {_MIN_BUFFER_BYTES / 1024**3:g}GiB headroom)"
        for scenario, stack, offload, shm in stacks
        if shm is not None
        and offload is not None
        and shm < offload + _MIN_BUFFER_BYTES
    ]


# ---------------------------------------------------------------------------
# The rule, against fixtures that own the cases
# ---------------------------------------------------------------------------

FIXTURE_BY_NAME = {
    stack: (offload, shm) for _s, stack, offload, shm in _offload_stacks([FIXTURE])
}


def test_fixture_discovery_finds_every_offloading_stack():
    """A rename in the walk above must not quietly shrink the sweep."""
    assert set(FIXTURE_BY_NAME) == {
        "shm-too-small",
        "shm-exact",
        "shm-buffer-too-thin",
        "shm-buffer-exact",
        "shm-ample",
        "nested-under-modelservice",
    }, FIXTURE_BY_NAME


@pytest.mark.parametrize("stack", ["shm-too-small", "shm-exact", "shm-buffer-too-thin"])
def test_undersized_shm_is_reported(stack):
    offload, shm = FIXTURE_BY_NAME[stack]
    assert _undersized([(FIXTURE, stack, offload, shm)])


@pytest.mark.parametrize("stack", ["shm-buffer-exact", "shm-ample"])
def test_sufficient_shm_passes(stack):
    offload, shm = FIXTURE_BY_NAME[stack]
    assert not _undersized([(FIXTURE, stack, offload, shm)])


def test_a_stack_that_does_not_offload_is_ignored():
    """no-offload caps dshm at 64Mi, which would fail if it were checked."""
    assert "no-offload" not in FIXTURE_BY_NAME


# ---------------------------------------------------------------------------
# Shipped scenarios: sized right, whatever the values are
# ---------------------------------------------------------------------------


def test_shipped_scenarios_size_shm_for_the_offload_region():
    problems = _undersized(list(_offload_stacks(SCENARIOS)))
    assert not problems, (
        "vLLM cannot create the offload region and the decode pod crashloops. "
        "Raise the dshm emptyDir sizeLimit to at least cpu_bytes_to_use:\n  "
        + "\n  ".join(problems)
    )
