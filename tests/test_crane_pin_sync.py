"""Validate crane pin consistency between install script and tracking docs."""

from __future__ import annotations

import re
from pathlib import Path


def test_crane_pin_synced_between_install_and_docs() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    install_sh = (repo_root / "install.sh").read_text(encoding="utf-8")
    upstream_versions = (repo_root / "docs" / "upstream-versions.md").read_text(
        encoding="utf-8",
    )

    install_match = re.search(
        r"install_crane_linux\(\)\s*\{[\s\S]*?local version=(v[0-9]+\.[0-9]+\.[0-9]+)",
        install_sh,
    )
    docs_match = re.search(r"\|\s+\*\*crane\*\*\s+\|\s+`([^`]+)`\s+\|", upstream_versions)

    assert install_match is not None
    assert docs_match is not None
    assert docs_match.group(1) == install_match.group(1)
