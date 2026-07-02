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
        router_chart_version: str = "v0",
        router_standalone_chart: str = "oci://ghcr.io/llm-d/charts/llm-d-router-standalone",
        router_gateway_chart: str = "oci://ghcr.io/llm-d/charts/llm-d-router-gateway",
    ):
        self._repo_path = Path(repo_path).resolve()
        self._accelerator_backend = accelerator_backend

        self._variables: dict[str, str] = {}
        if readme_variables:
            self._variables.update(readme_variables)
        # ROUTER_CHART_VERSION accompanies the migration off the
        # GAIE-published `inferencepool` / `standalone` charts onto the
        # llm-d-router-{gateway,standalone}-dev charts. Older guide READMEs
        # still rely on GAIE_VERSION for the inference extension CRDs
        # (which remain at v1.5.0 etc.), so both variables are exposed.
        # REPO_ROOT is what the guide README expects from
        # ``$(realpath $(git rev-parse --show-toplevel))`` -- since we know
        # the cloned repo path here, we force-set it so paths like
        # ``${REPO_ROOT}/guides/recipes/router/...`` substitute cleanly.
        self._variables.update(
            {
                "GUIDE_NAME": guide_name,
                "NAMESPACE": namespace,
                "GAIE_VERSION": gaie_version,
                "ROUTER_CHART_VERSION": router_chart_version,
                "ROUTER_STANDALONE_CHART": router_standalone_chart,
                "ROUTER_GATEWAY_CHART": router_gateway_chart,
                "REPO_ROOT": str(self._repo_path),
            }
        )
        # Override (or fill) the guide README's ${VAR} values; cannot add
        # variables the README does not reference, nor override the forced
        # GUIDE_NAME / NAMESPACE / GAIE_VERSION / ROUTER_CHART_VERSION /
        # REPO_ROOT below.
        if variable_overrides:
            self._variables.update(variable_overrides)

    def resolve(self, command: str) -> str:
        """Return *command* with all placeholders resolved and paths absolutised."""
        result = self._substitute_variables(command)
        result = self._absolutise_paths(result)
        result = self._apply_accelerator_backend(result)
        return result

    # ------------------------------------------------------------------

    # Guard against pathological cycles (`A -> ${B}`, `B -> ${A}`). Realistic
    # guides nest at most 2-3 levels deep (e.g. ROUTER_BASE_VALUES contains
    # ${REPO_ROOT}); 10 rounds is orders of magnitude above that.
    _MAX_SUBST_ROUNDS = 10

    def _substitute_variables(self, text: str) -> str:
        """Replace `${VAR}` placeholders, iterating until stable.

        Values in `self._variables` may themselves contain further `${VAR}`
        references (e.g. `ROUTER_BASE_VALUES=-f ${REPO_ROOT}/…`). A single
        `re.sub` pass would leave those nested refs unresolved, so we
        re-scan until the string stops changing (or the iteration cap trips,
        which means a cycle — we return the last state and let the caller
        deal with the unresolved `${VAR}` literal).
        """

        def _replace(m: re.Match) -> str:
            var_name = m.group(1)
            if var_name in self._variables:
                return self._variables[var_name]
            return m.group(0)

        for _ in range(self._MAX_SUBST_ROUNDS):
            new_text = _VAR_RE.sub(_replace, text)
            if new_text == text:
                return text
            text = new_text
        return text

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
