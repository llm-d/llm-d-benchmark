# Contributing to llm-d-benchmark

## Governance Structure

`llm-d-benchmark` adopts the following hierarchical technical governance structure:

- A community of **contributors** who file issues and submit pull requests
- A body of **core maintainers** who own `llm-d-benchmark` overall and drive its development
- A **lead core maintainer** who is the catch-all decision maker when consensus cannot be reached by core maintainers

All contributions are expected to follow `llm-d-benchmark` design principles and best practices, as enforced by core maintainers. While high-quality pull requests are appreciated and encouraged, all maintainers reserve the right to prioritize their own work over code reviews at-will, hence contributors should not expect their work to be reviewed promptly.

Contributors can maximize the chances of their work being accepted by maintainers by meeting a high quality bar before sending a PR to maintainers.

### Core maintainers

The core maintainers lead the development of `llm-d-benchmark` and define the benchmarking infrastructure and strategy for the broader `llm-d project`. Their responsibilities include:

- Proposing, implementing and reviewing load profiles, parameter configurations, run rules, data collections, and analysis of workloads to `llm-d`
- Enforcing code quality standards and adherence to core design principles

The core maintainers should publicly articulate their decision-making, and share the reasoning behind their decisions, vetoes, and dispute resolution.

List of core maintainers can be found in the [OWNERS](OWNERS) file.

### Lead core maintainer

When core maintainers cannot come to a consensus, a publicly declared lead maintainer is expected to settle the debate and make executive decisions.

The Lead Core Maintainer should publicly articulate their decision-making, and give a clear reasoning for their decisions.

The Lead Core Maintainer is also responsible for confirming or removing core maintainers.

#### Lead maintainer (as of 05/13/2025)

- [Marcio Silva](https://github.com/maugustosilva)

### Decision Making

#### Uncontroversial Changes

We are committed to accepting functional bug fixes that meet our quality standards – and include minimized unit tests to avoid future regressions. Performance improvements generally fall under the same category, with the caveat that they may be rejected if the trade-off between usefulness and complexity is deemed unfavorable by core maintainers. Design changes that neither fix known functional nor performance issues are automatically considered controversial.

#### Controversial Changes

More controversial design changes (e.g., breaking changes to workload profiles, load generators, run rules or data collection and analisys tools) are evaluated on a case-by-case basis under the subjective judgment of core maintainers.

## Submitting a Pull Request

We welcome contributions to any aspect of `llm-d-benchmark`! If you have a bug fix, feature request, or improvement, please submit a pull request (PR) to the repository.

For every Pull Request submitted, ensure the following steps have been done:

1. [Sign your commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits)
2. Make sure that the [pre-commit](https://pre-commit.com/) hooks have been run and pass **before you push** the contents of your PR. See [Local Development Checks](#local-development-checks-pre-commit) below.

### Local Development Checks (pre-commit)

The repository ships a [pre-commit](https://pre-commit.com/) configuration that runs the **same checks CI runs on your PR**, locally, before you push. Setting this up once means you catch regressions without waiting for CI and never push a PR that will fail the PR-benchmark or plan-rendering CI workflows.

#### One-time setup

```bash
./util/setup_precommit.sh
```

That script:

1. Delegates to `./install.sh` (no `-y` flag — we deliberately want a virtualenv, not system Python) to create/reuse `.venv/`, install the `llmdbenchmark` CLI and `planner` (from [llm-d-planner](https://github.com/llm-d-incubation/llm-d-planner)), and provision the required system tools (`helm`, `helmfile`, `kubectl`, `helm-diff`). This is the same bootstrap CI uses (see [`.github/workflows/ci-pr-benchmark.yaml`](.github/workflows/ci-pr-benchmark.yaml)), with `-y` omitted so local development stays in `.venv/` instead of polluting your system Python.
2. Installs `pre-commit`, `pytest`, `pytest-xdist`, `pytest-testmon`, and `detect-secrets` from [`.pre-commit_requirements.txt`](.pre-commit_requirements.txt).
3. Registers both the `pre-commit` and `pre-push` hook types.

You only need to run this once per clone. On subsequent invocations `install.sh` uses its `~/.llmdbench_dependencies_checked` cache to skip dependencies that have already been verified.

#### What runs, when, and why

| Hook | Stage | Command | Mirrors CI |
|---|---|---|---|
| `py-compile` | `pre-commit` | `python -m compileall -q llmdbenchmark` (only on changed `llmdbenchmark/**.py`) | — (fast local-only syntax gate) |
| `pytest` | `pre-push` | [`util/prepush_unit_tests.py`](util/prepush_unit_tests.py) — [testmon](https://www.testmon.org/) runs only the tests affected by the push; falls back to the full suite when it cannot | `unit-tests` job in [`ci-pr-benchmark.yaml`](.github/workflows/ci-pr-benchmark.yaml) |
| `render-validation-changed` | `pre-commit` | [`util/precommit_render_changed.py`](util/precommit_render_changed.py) — detects which scenarios the commit actually touched and renders only those (falls back to `cicd/kind` canary for shared-path changes) | Scoped subset of [`ci-pr-plan-rendering-validation.yaml`](.github/workflows/ci-pr-plan-rendering-validation.yaml) |
| `generate-sbom` | `pre-commit` | `python util/generate_sbom.py --check` (only when `install.sh`/`pyproject.toml`/`config/templates/values/defaults.yaml`/`util/generate_sbom.py` change) | — (local-only SBOM drift check) |
| `detect-secrets` | `pre-commit` | [`ibm/detect-secrets`](https://github.com/ibm/detect-secrets) against `.secrets.baseline` with `--use-all-plugins` | — (local-only secrets scan) |
| `ruff-check` | `pre-commit` | `ruff check --fix` | Ruff jobs in [`ci-pr-lint-and-format.yaml`](.github/workflows/ci-pr-lint-and-format.yaml) |
| `ruff-format` | `pre-commit` | `ruff format` | Ruff jobs in [`ci-pr-lint-and-format.yaml`](.github/workflows/ci-pr-lint-and-format.yaml) |

Stages explained:

- **`pre-commit`** fires on every `git commit`. Byte-compile, a **scoped** render of the scenarios your diff actually touched, an SBOM drift check, ruff lint/format, and a secrets scan. No unit tests here — they kept every commit slow, so they moved to pre-push.
- **`pre-push`** fires on every `git push` and runs the unit-test hook: testmon picks the tests affected by your pushed python changes; non-python files, deletions, or test-setup changes fall back to the full suite; a push with nothing test-relevant exits immediately. CI still runs the full suite on every PR.

##### How `pytest` picks what to run

[`util/prepush_unit_tests.py`](util/prepush_unit_tests.py) uses the push range pre-commit supplies and picks a mode:

1. Only `.py` files under `llmdbenchmark/`, `tests/`, `workload/`, `config/`, `benchmark-report/` changed → testmon runs only the affected tests (selection data lives in `.testmondata`, gitignored; the first run has no data and runs everything once).
2. Non-python files under those paths, deleted files under those paths, or a test-setup change (`pyproject.toml`, `uv.lock`, `install.sh`, `.pre-commit_requirements.txt`, `util/prepush_unit_tests.py`, `util/generate_sbom.py`) → full suite.
3. Nothing test-relevant in the push range (docs, `.github/`, ...) → exits 0 immediately.
4. Push range unknown (manual run without arguments) → full suite.

For manual debugging, pass files directly:

```bash
python util/prepush_unit_tests.py llmdbenchmark/parser/config_schema.py
```

testmon runs force `COVERAGE_CORE=ctrace`: the default `sysmon` core on Python 3.12+ cannot attribute coverage to individual tests.

##### How `render-validation-changed` picks scenarios

The helper script [`util/precommit_render_changed.py`](util/precommit_render_changed.py) receives the list of staged files from pre-commit and resolves them to specs using four rules, in order:

1. `config/specification/<path>.yaml.j2` → render `<path>`.
2. `config/scenarios/<path>.yaml` → render `<path>` (the `scenarios/` and `specification/` trees are kept 1:1).
3. Any change under a shared render path (`config/templates/`, `llmdbenchmark/{parser,plan,executor,utilities,standup,run,teardown,smoketests}/`, `llmdbenchmark/cli.py`, `llmdbenchmark/config.py`) → render the `cicd/kind` canary. We do **not** expand shared-path changes into every scenario locally — that's what CI's full per-spec render job does on the PR.
4. **Nothing resolved** (docs-only commit, test-only commit, anything else that doesn't touch a scenario or shared render path) → render the `cicd/kind` canary as a **baseline sanity check**. Every commit proves the render path is healthy, even when the diff has nothing to do with scenarios.

The script always prints exactly which scenarios it is about to render and why, e.g.:

```text
Rendering 2 scenarios:
  - cicd/kind                   [shared render path touched (llmdbenchmark/parser/version_resolver.py)]
  - guides/tiered-prefix-cache  [edited config/specification/guides/tiered-prefix-cache.yaml.j2]
Rendering: cicd/kind
Rendering: guides/tiered-prefix-cache
Render results: 2 passed, 0 failed
```

So: edit a single spec → that spec renders. Edit three specs → all three render. Edit a parser file → the canary renders. Edit docs only → the canary still renders as a baseline. The hook is never a no-op on pre-commit, which is the point — every commit gets proof that something renders cleanly.

#### Running hooks manually

```bash
# Run the full pre-commit suite against every tracked file (what CI effectively does):
pre-commit run --all-files

# Run one specific hook by id:
pre-commit run py-compile --all-files
pre-commit run render-validation-changed --all-files

# The pytest hook lives at the pre-push stage:
pre-commit run pytest --hook-stage pre-push

# Run against only the files in your current diff (what `git commit` does):
pre-commit run
```

#### Debugging a hook failure

If a hook fails, the first line of output tells you which hook and exits with the underlying tool's output. Common cases:

- **`pytest` failure** — run `pytest tests/ benchmark-report/tests/ -x -q -n4` directly; the CI `unit-tests` job runs the same command. To force the hook to run everything: `python util/prepush_unit_tests.py` with no arguments.
- **`render-validation-changed` failure** — the hook output prints which spec(s) it tried to render and their pass/fail status. Reproduce the failing spec interactively to see the full error:
  ```bash
  llmdbenchmark --spec <the-failing-spec> --dry-run plan -p debug
  ```
  Render failures are usually caused by Jinja template changes or missing keys in `config/scenarios/**/<name>.yaml`. If the hook says it rendered `cicd/kind` and you were expecting a different spec, it means your change touched a shared render path and the hook fell back to the canary — the exhaustive per-spec render runs in CI on the PR.
- **`detect-secrets` failure** — either your change added a secret (remove it) or added a new pattern the baseline doesn't know about. To update the baseline after reviewing the finding:
  ```bash
  detect-secrets scan --baseline .secrets.baseline --use-all-plugins
  detect-secrets audit .secrets.baseline
  ```

#### Bypassing hooks (use sparingly)

The hooks exist to catch regressions before they reach CI. Bypass only in genuine emergencies:

```bash
git commit --no-verify    # skips pre-commit stage
git push --no-verify      # skips pre-push stage
```

If you find yourself reaching for `--no-verify` to get around a legitimate bug, **fix the bug instead** — the same check will fail on CI when you open the PR.

#### Updating the hook configuration

The hooks are defined in [`.pre-commit-config.yaml`](.pre-commit-config.yaml). If you add a new hook there or change an existing one, re-run `./util/setup_precommit.sh` (or just `pre-commit install` — the config's `default_install_hook_types` registers both the pre-commit and pre-push stages) so git picks up the change.