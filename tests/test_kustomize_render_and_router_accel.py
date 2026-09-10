"""Tests for the render phase and the accelerator-aware router command filter.

Before these behaviours existed a guide's "Deploy the Render (Tokenizer)
Service" section inherited the model-server section -- where only a single
command is ever applied -- so the render Service was silently never created.
Router commands were likewise all executed, so a GPU run also applied a
guide's ``router/xpu.values.yaml`` override.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

from llmdbenchmark.kustomize.readme_parser import (
    CommandPhase,
    DeployMode,
    parse_guide_readme,
)

# See test_kustomize_deploy_logging.py: the `llmdbenchmark.standup.steps` package
# __init__ pulls in the top-level `planner` package, which is not a declared
# pyproject dependency. Load the step module directly instead.
_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "standup"
    / "steps"
    / "step_06_kustomize_deploy.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_06_kustomize_deploy_render_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_06_kustomize_deploy_render_isolated"] = _module
_spec.loader.exec_module(_module)
KustomizeDeployStep = _module.KustomizeDeployStep


def _write_readme(tmp_path: Path, body: str) -> Path:
    guide_dir = tmp_path / "test-guide"
    guide_dir.mkdir()
    readme = guide_dir / "README.md"
    readme.write_text(textwrap.dedent(body), encoding="utf-8")
    return readme


class _IdentityResolver:
    def resolve(self, raw: str) -> str:
        return raw


class _RecordingContext:
    def __init__(self):
        self.messages: list[str] = []
        outer = self

        class _Logger:
            @staticmethod
            def log_info(message: str) -> None:
                outer.messages.append(message)

        self.logger = _Logger()


class TestRenderPhase:
    def test_render_section_gets_its_own_phase(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            ### 3. Deploy the Model Server

            ```bash
            kubectl apply -n ns -k guides/g/modelserver/gpu/vllm/
            ```

            ### 4. Deploy the Render (Tokenizer) Service

            ```bash
            kubectl apply -n ns -k guides/g/render/
            ```
            """,
        )

        parsed = parse_guide_readme(readme)

        modelserver = [c.raw for c in parsed.get_commands(CommandPhase.MODELSERVER)]
        render = [c.raw for c in parsed.get_commands(CommandPhase.RENDER, DeployMode.ANY)]

        assert modelserver == ["kubectl apply -n ns -k guides/g/modelserver/gpu/vllm/"]
        assert render == ["kubectl apply -n ns -k guides/g/render/"]

    def test_render_commands_are_part_of_deploy_commands(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            ### Deploy the Render (Tokenizer) Service

            ```bash
            kubectl apply -n ns -k guides/g/render/
            ```
            """,
        )

        parsed = parse_guide_readme(readme)

        raws = [c.raw for c in parsed.get_deploy_commands()]
        assert "kubectl apply -n ns -k guides/g/render/" in raws


class TestRouterAcceleratorFilter:
    README = """\
        ### 1. Deploy the llm-d Router

        ```bash
        helm install g chart -f guides/g/router/g.values.yaml -n ns
        ```

        For Intel XPU, add the XPU router override:

        ```bash
        helm install g chart -f guides/g/router/g.values.yaml -f guides/g/router/xpu.values.yaml -n ns
        ```
        """

    def _router_commands(self, tmp_path: Path):
        parsed = parse_guide_readme(_write_readme(tmp_path, self.README))
        return parsed.get_commands(CommandPhase.ROUTER, DeployMode.STANDALONE)

    def test_gpu_run_skips_the_xpu_override(self, tmp_path: Path):
        commands = self._router_commands(tmp_path)
        context = _RecordingContext()

        selected = KustomizeDeployStep._select_router_commands(
            commands, "gpu/vllm", _IdentityResolver(), context
        )

        assert len(commands) == 2
        assert [c.raw for c in selected] == [commands[0].raw]
        assert any("skipping" in m for m in context.messages)

    def test_xpu_run_keeps_every_command(self, tmp_path: Path):
        commands = self._router_commands(tmp_path)

        selected = KustomizeDeployStep._select_router_commands(
            commands, "xpu/vllm", _IdentityResolver(), _RecordingContext()
        )

        assert [c.raw for c in selected] == [c.raw for c in commands]

    def test_guide_named_values_file_is_never_treated_as_accelerator(
        self, tmp_path: Path
    ):
        readme = _write_readme(
            tmp_path,
            """\
            ### 1. Deploy the llm-d Router

            ```bash
            helm install g chart -f guides/g/router/wide-ep-lws.values.yaml -n ns
            ```
            """,
        )
        commands = parse_guide_readme(readme).get_commands(
            CommandPhase.ROUTER, DeployMode.STANDALONE
        )

        selected = KustomizeDeployStep._select_router_commands(
            commands, "xpu/vllm", _IdentityResolver(), _RecordingContext()
        )

        assert len(selected) == 1

    def test_rocm_backend_keeps_the_amd_override(self, tmp_path: Path):
        readme = _write_readme(
            tmp_path,
            """\
            ### 1. Deploy the llm-d Router

            ```bash
            helm install g chart -f guides/g/router/amd.values.yaml -n ns
            ```
            """,
        )
        commands = parse_guide_readme(readme).get_commands(
            CommandPhase.ROUTER, DeployMode.STANDALONE
        )

        selected = KustomizeDeployStep._select_router_commands(
            commands, "rocm/vllm", _IdentityResolver(), _RecordingContext()
        )

        assert len(selected) == 1
