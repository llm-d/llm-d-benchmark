"""``model.revision`` pins the HuggingFace revision step 04 downloads.

Without a revision, the download Job (PVC) and the download DaemonSet
(hostPath) fetch the repo's default branch, so a re-run can stage different
weights, config or tokenizer files than the previous one. Setting
``model.revision`` passes it to ``hf download --revision`` in both.

Behavior under test:
- With ``model.revision`` set, the Job and the DaemonSet export it as
  ``HF_MODEL_REVISION`` and pass ``--revision "${HF_MODEL_REVISION}"``.
- Without it, neither the variable nor the flag is rendered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

_ROOT = Path(__file__).resolve().parents[1]
_SCENARIO = _ROOT / "config" / "scenarios" / "guides" / "optimized-baseline.yaml"
_REVISION = "0123456789abcdef0123456789abcdef01234567"
_HOSTPATH = {"storage": {"hostPath": {"enabled": True, "path": "/mnt/models"}}}


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, *_: Any, **__: Any) -> None:
        pass

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


def _render(tmp_path: Path, overrides: dict) -> Path:
    result = RenderPlans(
        template_dir=_ROOT / "config" / "templates" / "jinja",
        defaults_file=_ROOT / "config" / "templates" / "values" / "defaults.yaml",
        scenarios_file=_SCENARIO,
        output_dir=tmp_path / "plan",
        logger=_Logger(),
        setup_overrides=overrides,
    ).eval()
    assert not result.has_errors
    return tmp_path / "plan" / "optimized-baseline"


def _downloader(tmp_path: Path, overrides: dict, hostpath: bool) -> dict:
    if hostpath:
        overrides = {**overrides, **_HOSTPATH}
    plan = _render(tmp_path, overrides)
    name, kind = (
        ("03_download_daemonset.yaml", "DaemonSet")
        if hostpath
        else ("04_download_job.yaml", "Job")
    )
    docs = yaml.safe_load_all((plan / name).read_text(encoding="utf-8"))
    workload = next(d for d in docs if d and d["kind"] == kind)
    return workload["spec"]["template"]["spec"]["containers"][0]


@pytest.mark.parametrize("hostpath", [False, True], ids=["job", "daemonset"])
def test_revision_is_passed_to_hf_download(tmp_path: Path, hostpath: bool) -> None:
    container = _downloader(tmp_path, {"model": {"revision": _REVISION}}, hostpath)

    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["HF_MODEL_REVISION"] == _REVISION
    script = container["args"][0]
    assert 'hf download "${HF_MODEL_ID}" --revision "${HF_MODEL_REVISION}" ' in script


@pytest.mark.parametrize("hostpath", [False, True], ids=["job", "daemonset"])
def test_no_revision_downloads_default_branch(tmp_path: Path, hostpath: bool) -> None:
    container = _downloader(tmp_path, {}, hostpath)

    assert "HF_MODEL_REVISION" not in {e["name"] for e in container["env"]}
    script = container["args"][0]
    assert "hf download" in script
    assert "--revision" not in script
