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

1. Delegates to `./install.sh` (no `-y` flag — we deliberately want a virtualenv, not system Python) to create/reuse `.venv/`, install the `llmdbenchmark` CLI and `config_explorer` editably, and provision all required system tools (`helm`, `helmfile`, `kubectl`, `skopeo`, `crane`, `helm-diff`, `jq`, `yq`, `kustomize`). This is the same bootstrap CI uses (see [`.github/workflows/ci-pr-benchmark.yaml`](.github/workflows/ci-pr-benchmark.yaml)), with `-y` omitted so local development stays in `.venv/` instead of polluting your system Python.
2. Installs `pre-commit`, `pytest`, and `detect-secrets` from [`.pre-commit_requirements.txt`](.pre-commit_requirements.txt).
3. Registers both the `pre-commit` and `pre-push` hook types.

You only need to run this once per clone. On subsequent invocations `install.sh` uses its `~/.llmdbench_dependencies_checked` cache to skip dependencies that have already been verified.

#### What runs, when, and why

| Hook | Stage | Command | Mirrors CI |
|---|---|---|---|
| `py-compile` | `pre-commit`, `pre-push` | `python -m compileall -q llmdbenchmark` (only on changed `llmdbenchmark/**.py`) | — (fast local-only syntax gate) |
| `pytest` | `pre-commit`, `pre-push` | `python -m pytest tests/ -x -q` | `unit-tests` job in [`ci-pr-benchmark.yaml`](.github/workflows/ci-pr-benchmark.yaml) |
| `render-validation-changed` | `pre-commit`, `pre-push` | [`util/precommit_render_changed.py`](util/precommit_render_changed.py) — detects which scenarios the commit actually touched and renders only those (falls back to `cicd/kind-sim` canary for shared-path changes) | Scoped subset of [`ci-pr-plan-rendering-validation.yaml`](.github/workflows/ci-pr-plan-rendering-validation.yaml) |
| `render-validation-all` | **`pre-push` only** | Loop over every `config/specification/**/*.yaml.j2` and dry-run plan | Full [`ci-pr-plan-rendering-validation.yaml`](.github/workflows/ci-pr-plan-rendering-validation.yaml) |
| `detect-secrets` | `pre-commit` | [`ibm/detect-secrets`](https://github.com/ibm/detect-secrets) against `.secrets.baseline` with `--use-all-plugins` | — (local-only secrets scan) |

Stages explained:

- **`pre-commit`** fires on every `git commit`. Only fast checks live here — byte-compile, unit tests, and a **scoped** render of whichever scenarios your diff actually touched. This is your "catch the typo" layer.
- **`pre-push`** fires on every `git push`. The full render-validation loop across every specification runs here. This is your "catch the scenario regression" layer and matches the CI render job exactly.

Splitting the layers keeps `git commit` fast enough to use naturally while still guaranteeing that nothing broken ever leaves your machine.

##### How `render-validation-changed` picks scenarios

The helper script [`util/precommit_render_changed.py`](util/precommit_render_changed.py) receives the list of staged files from pre-commit and resolves them to specs using four rules, in order:

1. `config/specification/<path>.yaml.j2` → render `<path>`.
2. `config/scenarios/<path>.yaml` → render `<path>` (the `scenarios/` and `specification/` trees are kept 1:1).
3. Any change under a shared render path (`config/templates/`, `llmdbenchmark/{parser,plan,executor,utilities,standup,run,teardown,smoketests}/`, `llmdbenchmark/cli.py`, `llmdbenchmark/config.py`) → render the `cicd/kind-sim` canary. We do **not** expand shared-path changes into every scenario on every commit — that's what `render-validation-all` does on pre-push.
4. **Nothing resolved** (docs-only commit, test-only commit, anything else that doesn't touch a scenario or shared render path) → render the `cicd/kind-sim` canary as a **baseline sanity check**. Every commit proves the render path is healthy, even when the diff has nothing to do with scenarios.

The script always prints exactly which scenarios it is about to render and why, e.g.:

```text
Rendering 2 scenarios:
  - cicd/kind-sim          [shared render path touched (llmdbenchmark/parser/version_resolver.py)]
  - guides/tiered-prefix-cache  [edited config/specification/guides/tiered-prefix-cache.yaml.j2]
Rendering: cicd/kind-sim
Rendering: guides/tiered-prefix-cache
Render results: 2 passed, 0 failed
```

So: edit a single spec → that spec renders. Edit three specs → all three render. Edit a parser file → the canary renders. Edit docs only → the canary still renders as a baseline. The hook is never a no-op on pre-commit, which is the point — every commit gets proof that something renders cleanly.

#### Running hooks manually

```bash
# Run the full pre-commit suite against every tracked file (what CI effectively does):
pre-commit run --all-files

# Run one specific hook by id:
pre-commit run pytest --all-files
pre-commit run render-validation-changed --all-files
pre-commit run render-validation-all --all-files --hook-stage pre-push

# Run against only the files in your current diff (what `git commit` does):
pre-commit run
```

#### Debugging a hook failure

If a hook fails, the first line of output tells you which hook and exits with the underlying tool's output. Common cases:

- **`pytest` failure** — run `pytest tests/ -x -q` directly and fix the failing test. The CI `unit-tests` job runs exactly this command, so a fix here is guaranteed to green CI.
- **`render-validation-*` failure** — the hook output prints which spec(s) it tried to render and their pass/fail status. Reproduce the failing spec interactively to see the full error:
  ```bash
  llmdbenchmark --spec <the-failing-spec> --dry-run plan -p debug
  ```
  Render failures are usually caused by Jinja template changes or missing keys in `config/scenarios/**/<name>.yaml`. If `render-validation-changed` says it rendered `cicd/kind-sim` and you were expecting a different spec, it means your change touched a shared render path and the hook fell back to the canary — push the change and let `render-validation-all` exercise every spec.
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

The hooks are defined in [`.pre-commit-config.yaml`](.pre-commit-config.yaml). If you add a new hook there or change an existing one, re-run `./util/setup_precommit.sh` (or just `pre-commit install && pre-commit install --hook-type pre-push`) so git picks up the change.