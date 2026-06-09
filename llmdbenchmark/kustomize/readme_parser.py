"""Parse deployment commands from llm-d guide README.md files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CommandPhase(str, Enum):
    PREREQUISITES = "prerequisites"
    ROUTER = "router"
    MODELSERVER = "modelserver"
    MONITORING = "monitoring"
    VERIFY = "verify"
    CLEANUP = "cleanup"


class DeployMode(str, Enum):
    STANDALONE = "standalone"
    GATEWAY = "gateway"
    ANY = "any"


@dataclass
class GuideCommand:
    """A single extracted command with its phase and deployment mode."""

    raw: str
    phase: CommandPhase
    mode: DeployMode = DeployMode.ANY

    def __str__(self) -> str:
        return self.raw


_EXPORT_RE = re.compile(r"""^export\s+(\w+)=["']?([^"'\s#$()]+)["']?""")


@dataclass
class ParsedGuide:
    """Parsed deployment commands from a guide README."""

    guide_name: str
    commands: list[GuideCommand] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)

    def get_commands(
        self,
        phase: CommandPhase,
        mode: DeployMode = DeployMode.STANDALONE,
    ) -> list[GuideCommand]:
        """Return commands matching *phase* and compatible with *mode*."""
        return [
            c
            for c in self.commands
            if c.phase == phase and c.mode in (mode, DeployMode.ANY)
        ]

    def get_deploy_commands(
        self, mode: DeployMode = DeployMode.STANDALONE
    ) -> list[GuideCommand]:
        """Return all deployment commands (prerequisites + router + modelserver + monitoring)."""
        phases = (
            CommandPhase.PREREQUISITES,
            CommandPhase.ROUTER,
            CommandPhase.MODELSERVER,
            CommandPhase.MONITORING,
        )
        return [
            c
            for c in self.commands
            if c.phase in phases and c.mode in (mode, DeployMode.ANY)
        ]

    def get_cleanup_commands(
        self, mode: DeployMode = DeployMode.STANDALONE
    ) -> list[GuideCommand]:
        return self.get_commands(CommandPhase.CLEANUP, mode)

    def get_verify_commands(
        self, mode: DeployMode = DeployMode.STANDALONE
    ) -> list[GuideCommand]:
        return self.get_commands(CommandPhase.VERIFY, mode)


# ---------------------------------------------------------------------------
# Internal state machine
# ---------------------------------------------------------------------------


class _Section(str, Enum):
    PREAMBLE = "preamble"
    PREREQUISITES = "prerequisites"
    ROUTER_STANDALONE = "router_standalone"
    ROUTER_GATEWAY = "router_gateway"
    MODELSERVER = "modelserver"
    MONITORING = "monitoring"
    VERIFICATION = "verification"
    CLEANUP = "cleanup"
    IGNORED = "ignored"


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_CODE_FENCE_START = re.compile(r"^\s*```(?:bash|sh|shell)?\s*$")
_CODE_FENCE_END = re.compile(r"^\s*```\s*$")
_DETAILS_OPEN = re.compile(r"<details>", re.IGNORECASE)
_DETAILS_CLOSE = re.compile(r"</details>", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"<summary[^>]*>(.*?)</summary>", re.IGNORECASE | re.DOTALL)

# Skip-region markers.  Guide authors can wrap one or more bash blocks in
# ``<!-- llm-d-cicd:skip start -->`` / ``<!-- llm-d-cicd:skip end -->`` to
# instruct this parser to ignore them entirely: no GuideCommand is
# emitted and no ``export`` variables are harvested.  The markers
# themselves are standard HTML comments, so they render as nothing on
# GitHub.  Whitespace inside the comment is tolerated, and matching is
# case-insensitive.
_SKIP_BLOCK_START = re.compile(
    r"<!--\s*llm-d-cicd\s*:\s*skip\s+start\s*-->", re.IGNORECASE
)
_SKIP_BLOCK_END = re.compile(r"<!--\s*llm-d-cicd\s*:\s*skip\s+end\s*-->", re.IGNORECASE)

_SKIP_PREFIXES = (
    "export ",
    "git clone",
    "git checkout",
    "cd ",
    "curl ",
    "envsubst",
    "chmod ",
    "#",
)

_KEEP_PREFIXES = ("kubectl", "helm", "kustomize", "oc ")


def _classify_heading(text: str, current: _Section) -> _Section:
    """Map a heading string to a parser section."""
    lower = text.lower().strip()

    if "prerequisite" in lower:
        return _Section.PREREQUISITES
    if "cleanup" in lower or "uninstall" in lower or "removal" in lower:
        return _Section.CLEANUP
    if (
        "verification" in lower
        or "verify" in lower
        or "test request" in lower
        or "send test" in lower
    ):
        return _Section.VERIFICATION
    if "monitoring" in lower:
        return _Section.MONITORING
    if "deploy the model server" in lower or "model server" in lower:
        return _Section.MODELSERVER
    if "standalone mode" in lower or "standalone" in lower:
        if current in (_Section.PREAMBLE, _Section.PREREQUISITES):
            return current
        return _Section.ROUTER_STANDALONE
    if (
        "deploy the llm-d router" in lower
        or "deploy the router" in lower
        or "router" in lower
    ):
        return _Section.ROUTER_STANDALONE
    if "benchmark" in lower or "report" in lower:
        return _Section.IGNORED
    if "installation" in lower:
        return current

    return current


def _section_to_phase_mode(
    section: _Section,
) -> tuple[CommandPhase, DeployMode] | None:
    mapping = {
        _Section.PREREQUISITES: (CommandPhase.PREREQUISITES, DeployMode.ANY),
        _Section.ROUTER_STANDALONE: (CommandPhase.ROUTER, DeployMode.STANDALONE),
        _Section.ROUTER_GATEWAY: (CommandPhase.ROUTER, DeployMode.GATEWAY),
        _Section.MODELSERVER: (CommandPhase.MODELSERVER, DeployMode.ANY),
        _Section.MONITORING: (CommandPhase.MONITORING, DeployMode.ANY),
        _Section.VERIFICATION: (CommandPhase.VERIFY, DeployMode.ANY),
        _Section.CLEANUP: (CommandPhase.CLEANUP, DeployMode.ANY),
    }
    return mapping.get(section)


def _should_keep_command(line: str) -> bool:
    """True if the line is a deployable command we should capture."""
    stripped = line.strip()
    if not stripped:
        return False
    if any(stripped.startswith(p) for p in _SKIP_PREFIXES):
        return False
    if any(stripped.startswith(p) for p in _KEEP_PREFIXES):
        return True
    return False


def _join_continuations(lines: list[str]) -> list[str]:
    """Join lines ending with ``\\`` into single commands."""
    result: list[str] = []
    buf = ""
    for line in lines:
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
        else:
            buf += stripped.strip()
            if buf:
                result.append(buf)
            buf = ""
    if buf:
        result.append(buf)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_guide_readme(readme_path: Path, guide_name: str | None = None) -> ParsedGuide:
    """Parse a guide README.md and return structured deployment commands.

    Args:
        readme_path: Absolute path to the guide's README.md file.
        guide_name: Optional override; defaults to the parent directory name.

    Returns:
        A ``ParsedGuide`` with categorised commands.

    Skip regions:
        Bash blocks wrapped between ``<!-- llm-d-cicd:skip start -->`` and
        ``<!-- llm-d-cicd:skip end -->`` are ignored: no commands are
        emitted and no ``export`` variables are harvested from them.
        Heading and ``<details>`` state continue to update inside a skip
        region so section transitions remain correct.  The markers must
        appear outside of fenced code blocks (HTML comments inside a
        fence are literal text and are not interpreted).
    """
    if guide_name is None:
        guide_name = readme_path.parent.name

    text = readme_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    parsed = ParsedGuide(guide_name=guide_name, variables={})
    section = _Section.PREAMBLE
    in_code_block = False
    code_lines: list[str] = []

    # <details> tracking
    details_depth = 0
    details_mode: DeployMode | None = None
    saved_section: _Section | None = None

    # llm-d-cicd:skip region tracking.  When True, completed fenced
    # blocks are discarded instead of yielding GuideCommands / variables.
    skip_active = False

    for line in lines:
        # --- llm-d-cicd:skip markers (honored only outside code fences) ---
        if not in_code_block:
            if _SKIP_BLOCK_END.search(line):
                skip_active = False
                continue
            if _SKIP_BLOCK_START.search(line):
                skip_active = True
                continue

        # --- <details> / </details> handling ---
        if _DETAILS_OPEN.search(line):
            details_depth += 1
            if details_depth == 1:
                saved_section = section
            continue

        if _DETAILS_CLOSE.search(line):
            if details_depth > 0:
                details_depth -= 1
            if details_depth == 0:
                details_mode = None
                if saved_section is not None:
                    section = saved_section
                    saved_section = None
            continue

        # Detect <summary> inside <details>
        summary_match = _SUMMARY_RE.search(line)
        if summary_match and details_depth > 0:
            summary_text = summary_match.group(1).lower()
            if "gateway" in summary_text:
                details_mode = DeployMode.GATEWAY
                section = _Section.ROUTER_GATEWAY
            elif "accelerator" in summary_text or "other" in summary_text:
                details_mode = DeployMode.ANY
            elif "nccl" in summary_text or "gke" in summary_text:
                details_mode = DeployMode.ANY
            elif "monitoring" in summary_text:
                section = _Section.MONITORING
            continue

        # --- Code fence handling ---
        if _CODE_FENCE_START.match(line) and not in_code_block:
            in_code_block = True
            code_lines = []
            continue

        if _CODE_FENCE_END.match(line) and in_code_block:
            in_code_block = False
            if skip_active:
                # Block is inside a llm-d-cicd:skip region -- drop
                # everything (no commands, no harvested variables).
                code_lines = []
                continue
            joined = _join_continuations(code_lines)
            for cmd_text in joined:
                export_match = _EXPORT_RE.match(cmd_text.strip())
                if export_match:
                    parsed.variables[export_match.group(1)] = export_match.group(2)
            pm = _section_to_phase_mode(section)
            if pm is not None:
                phase, default_mode = pm
                for cmd_text in joined:
                    if _should_keep_command(cmd_text):
                        mode = (
                            details_mode if details_mode is not None else default_mode
                        )
                        parsed.commands.append(
                            GuideCommand(raw=cmd_text, phase=phase, mode=mode)
                        )
            code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # --- Heading transitions ---
        heading_match = _HEADING_RE.match(line)
        if heading_match and details_depth == 0:
            heading_text = heading_match.group(2)
            section = _classify_heading(heading_text, section)

    return parsed
