# Tests

## Prerequisites

```bash
pip install pytest pydantic
```

## Running

From the project root:

```bash
pytest tests/ -v
```

## Test Suites

### `test_config_schema.py`

Validates the pydantic config schema (`llmdbenchmark/parser/config_schema.py`) against the real defaults and scenario files.

| Test class | What it covers |
|---|---|
| `TestDefaultsValidation` | `defaults.yaml` passes validation, produces expected model values |
| `TestScenarioValidation` | Every scenario in `config/scenarios/` (merged with defaults) passes validation |
| `TestTypoDetection` | Misspelled keys in `decode`, `model`, `vllmCommon`, `harness`, `prefill.vllm` are caught |
| `TestTypeErrors` | Constraint violations (`gpuMemoryUtilization > 1`, negative `replicas`, negative `waitTimeout`) |
| `TestNonBlocking` | `validate_config()` returns a list on valid, invalid, garbage, and empty input — never raises |
| `TestAllowSections` | `extra="allow"` sections accept arbitrary keys (GPU resources, flags, top-level) |
| `TestScenarioOnlyFields` | Fields used by scenarios but absent from defaults are accepted |

## Keeping Tests in Sync

The config schema validates `defaults.yaml` and all scenario files automatically. When you make changes to templates or config, run the tests to catch regressions.

### Adding a new scenario

`TestScenarioValidation` auto-discovers every `*.yaml` file under `config/scenarios/examples/` and `config/scenarios/guides/`. New scenarios are picked up automatically — no test changes needed.

If the new scenario introduces a key that doesn't exist in `defaults.yaml` or the schema, the test will fail with a validation warning showing the unrecognized key. To fix:

1. Add the field to the appropriate model in `llmdbenchmark/parser/config_schema.py` (e.g. `VllmCommonConfig`, `DeploymentBaseConfig`, etc.)
2. Use `Optional` with a `None` default for fields that aren't in `defaults.yaml`
3. Add a targeted test in `TestScenarioOnlyFields` to document the field

### Adding a new key to `defaults.yaml`

1. Add the field to the corresponding pydantic model in `config_schema.py`
2. Match the type and default value from `defaults.yaml`
3. Run `pytest tests/ -v` to confirm defaults still pass

If the key is in a section with `STRICT_CONFIG` (`extra="forbid"`), omitting it from the schema will cause `TestDefaultsValidation` to fail.

### Adding a new config section to the schema

The schema is designed for incremental adoption. To model a new top-level section (e.g. `standalone`, `storage`, `gateway`):

1. Define the pydantic model(s) in `config_schema.py` using `STRICT_CONFIG`
2. Add the field to `BenchmarkConfig` (the root model)
3. Run `pytest tests/ -v` — this validates the new model against defaults and all scenarios
4. If scenarios use keys not in defaults for this section, add them as optional fields
5. For sections that accept arbitrary user-defined keys (like `pluginsCustomConfig`), use `LENIENT_CONFIG`

### Adding a new Jinja template

Templates consume the merged config dict. The schema doesn't validate templates directly, but it ensures the config feeding them is well-formed. If a new template requires new config keys:

1. Add the keys to `defaults.yaml`
2. Add corresponding fields to the schema (see "Adding a new key" above)
3. The existing `TestScenarioValidation` will catch any scenario that sets these keys incorrectly

### When tests fail

- **`TestDefaultsValidation` fails**: A key was added/renamed/removed in `defaults.yaml` but not in the schema
- **`TestScenarioValidation` fails for a specific scenario**: That scenario uses a key the schema doesn't recognize — add it to the model as optional
- **`TestTypoDetection` fails**: The schema is too lenient for that section — check if `STRICT_CONFIG` is applied
- **`TestAllowSections` fails**: A section that should be extensible is using `STRICT_CONFIG` instead of `LENIENT_CONFIG`
