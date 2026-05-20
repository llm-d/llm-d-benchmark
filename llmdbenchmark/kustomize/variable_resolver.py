"""Resolve ``${VAR}`` placeholders and relative paths in parsed guide commands."""

from __future__ import annotations

import re
from pathlib import Path


_VAR_RE = re.compile(r"\$\{(\w+)\}")
_RELATIVE_GUIDE_PATH = re.compile(r"(?<!\S)(guides/\S+)")


class GuideVariableResolver:
    """Replace ``${VAR}`` placeholders and resolve relative ``guides/...`` paths."""

    def __init__(
        self,
        guide_name: str,
        namespace: str,
        gaie_version: str,
        repo_path: str,
        accelerator_backend: str = "gpu/vllm",
        variable_overrides: dict[str, str] | None = None,
        readme_variables: dict[str, str] | None = None,
    ):
        self._variables: dict[str, str] = {}
        if readme_variables:
            self._variables.update(readme_variables)
        # Override (or fill) the guide README's ${VAR} values; cannot add
        # variables the README does not reference, nor override the forced
        # GUIDE_NAME / NAMESPACE / GAIE_VERSION set below.
        if variable_overrides:
            self._variables.update(variable_overrides)
        self._variables.update({
            "GUIDE_NAME": guide_name,
            "NAMESPACE": namespace,
            "GAIE_VERSION": gaie_version,
        })

        self._repo_path = Path(repo_path).resolve()
        self._accelerator_backend = accelerator_backend

    def resolve(self, command: str) -> str:
        """Return *command* with all placeholders resolved and paths absolutised."""
        result = self._substitute_variables(command)
        result = self._absolutise_paths(result)
        result = self._apply_accelerator_backend(result)
        return result

    # ------------------------------------------------------------------

    def _substitute_variables(self, text: str) -> str:
        def _replace(m: re.Match) -> str:
            var_name = m.group(1)
            if var_name in self._variables:
                return self._variables[var_name]
            return m.group(0)

        return _VAR_RE.sub(_replace, text)

    def _absolutise_paths(self, text: str) -> str:
        """Convert relative ``guides/...`` paths to absolute paths."""

        def _rewrite(m: re.Match) -> str:
            rel = m.group(1)
            return str(self._repo_path / rel)

        return _RELATIVE_GUIDE_PATH.sub(_rewrite, text)

    def _apply_accelerator_backend(self, text: str) -> str:
        """Swap the default ``gpu/vllm`` backend for the configured one."""
        if self._accelerator_backend == "gpu/vllm":
            return text
        return text.replace(
            "modelserver/gpu/vllm", f"modelserver/{self._accelerator_backend}"
        )
