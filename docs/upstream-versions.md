# Upstream Dependency Version Tracking

> This file is the source of truth for the [upstream dependency monitor](../.github/workflows/upstream-monitor.md) workflow.
> Add your project's key upstream dependencies below. The monitor runs daily and creates GitHub issues when breaking changes are detected.

## Dependencies

<!-- Add your tracked dependencies using the format below. Remove this comment when populated. -->

| Dependency | Current Pin | Pin Type | File Location | Upstream Repo |
|-----------|-------------|----------|---------------|---------------|
| **kubectl** | `v1.31.4` | tag | `scripts/setup_clients.sh` | kubernetes/kubernetes |
<!-- | **example-lib** | `v1.2.3` | tag | `go.mod` line 10 | example-org/example-lib | -->
