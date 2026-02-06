---
name: refine-mappings
description: Capture and review mapping refinements discovered during guide conversions
---

# Refine Guide Converter Mappings

## Purpose

This skill observes guide conversion sessions and captures recommended mapping changes for review. It helps maintain and improve the mapping rules in `docs/guide-converter-mappings.md` over time.

## Usage

```
/refine-mappings review
/refine-mappings capture <helm-path> -> <llmdbench-var>
/refine-mappings apply
/refine-mappings clear
```

## Workflow

### Automatic Observation Mode

When you run `/convert-guide`, this skill should be invoked afterward to capture learnings:

1. **After a conversion**, run `/refine-mappings review` to analyze what was discovered
2. The skill will identify:
   - Unmapped Helm values that appeared as comments
   - Values that required manual intervention
   - Patterns that suggest new mappings

### Manual Capture Mode

During or after a conversion, capture specific mappings:

```
/refine-mappings capture decode.nodeSelector -> LLMDBENCH_VLLM_COMMON_AFFINITY
```

This adds the mapping to a pending review queue.

## Review Process

### Step 1: Collect Pending Mappings

When `/refine-mappings review` is invoked:

1. **Read the conversion context** - Look at recent conversation for:
   - Helm values that couldn't be mapped
   - Manual overrides the user specified
   - Comments about missing mappings

2. **Check existing mappings** - Read `docs/guide-converter-mappings.md` to avoid duplicates

3. **Read env.sh for variable discovery** - Check `setup/env.sh` for potential target variables

4. **Present findings** in this format:

```
=== Mapping Refinement Review ===

## New Mappings Discovered

| # | Helm Path | Suggested LLMDBENCH Variable | Confidence | Source |
|---|-----------|------------------------------|------------|--------|
| 1 | `decode.nodeSelector` | `LLMDBENCH_VLLM_COMMON_AFFINITY` | High | Pattern match |
| 2 | `decode.tolerations` | `LLMDBENCH_VLLM_COMMON_TOLERATIONS` | Medium | Name similarity |
| 3 | `prefill.priorityClassName` | (unknown) | Low | No match found |

## Recommended Actions

1. **Accept mapping #1** - Clear pattern, exists in env.sh
2. **Verify mapping #2** - Check if TOLERATIONS variable exists
3. **Skip mapping #3** - May need new variable or is unmappable

Which mappings would you like to apply? (e.g., "1,2" or "all" or "none")
```

### Step 2: User Selection

Wait for user to specify which mappings to apply.

### Step 3: Apply Selected Mappings

When `/refine-mappings apply` is run (or user confirms selections):

1. **Read current mappings file**: `docs/guide-converter-mappings.md`

2. **Determine correct section** for each mapping:
   - `modelArtifacts.*` → Model Configuration
   - `decode.*` → Decode Stage
   - `prefill.*` → Prefill Stage
   - `routing.*`, `gateway.*` → GAIE / Infrastructure
   - `--*` (args) → vLLM Launch Arguments
   - `*.volumes*`, `*.volumeMounts*` → Shared Memory and Volumes

3. **Add new rows** to the appropriate tables

4. **Show diff** before writing:

```
=== Proposed Changes to docs/guide-converter-mappings.md ===

## Decode Stage (adding 1 row)

+ | `decode.nodeSelector` | `LLMDBENCH_VLLM_COMMON_AFFINITY` | `vllm_common_affinity` | Node selection for GPU type |

Apply these changes? (yes/no)
```

5. **Write the file** on confirmation

6. **Optionally update portable doc** - Ask if `docs/guide-converter.md` should also be updated

### Step 4: Clear Queue

Run `/refine-mappings clear` to reset the pending mappings queue.

## Inferring Mappings

When analyzing unmapped values, use these heuristics:

### Pattern Matching

| Helm Pattern | LLMDBENCH Pattern |
|--------------|-------------------|
| `decode.<field>` | `LLMDBENCH_VLLM_MODELSERVICE_DECODE_<FIELD>` |
| `prefill.<field>` | `LLMDBENCH_VLLM_MODELSERVICE_PREFILL_<FIELD>` |
| `modelArtifacts.<field>` | `LLMDBENCH_DEPLOY_<FIELD>` or `LLMDBENCH_VLLM_COMMON_<FIELD>` |
| `routing.<field>` | `LLMDBENCH_VLLM_MODELSERVICE_<FIELD>` |
| `--<arg-name>` | `LLMDBENCH_VLLM_COMMON_<ARG_NAME>` |

### Name Transformations

- `camelCase` → `UPPER_SNAKE_CASE`
- `parallelism.tensor` → `TENSOR_PARALLELISM`
- `containers[0].resources.requests.cpu` → `CPU_NR`
- `containers[0].resources.requests.memory` → `CPU_MEM`

### Confidence Levels

- **High**: Exact match found in env.sh, or follows established pattern
- **Medium**: Similar name exists, pattern partially matches
- **Low**: No match found, may need new variable or be unmappable

## Tracking State

The skill maintains state within the conversation by noting:

```
[MAPPING_QUEUE]
- decode.nodeSelector -> LLMDBENCH_VLLM_COMMON_AFFINITY (High, pending)
- decode.tolerations -> LLMDBENCH_VLLM_COMMON_TOLERATIONS (Medium, pending)
[/MAPPING_QUEUE]
```

This state is used by subsequent `/refine-mappings` commands in the same session.

## Example Session

```
User: /convert-guide https://github.com/llm-d/llm-d/tree/main/guides/new-guide

Claude: [Converts guide, notes some unmapped values as comments]
        ...
        # UNMAPPED: decode.nodeSelector = {"gpu.nvidia.com/model": "H100"}
        # UNMAPPED: decode.tolerations = [{"key": "nvidia.com/gpu", ...}]
        ...

User: /refine-mappings review

Claude: === Mapping Refinement Review ===

        Found 2 unmapped values from recent conversion:

        | # | Helm Path | Suggested Variable | Confidence |
        |---|-----------|-------------------|------------|
        | 1 | decode.nodeSelector | LLMDBENCH_VLLM_COMMON_AFFINITY | High |
        | 2 | decode.tolerations | (new: LLMDBENCH_VLLM_COMMON_TOLERATIONS) | Medium |

        Mapping #1 matches existing variable in env.sh.
        Mapping #2 would require adding a new variable.

        Apply which? (1, 2, all, none)

User: 1

Claude: Adding to docs/guide-converter-mappings.md:

        ## Decode Stage
        + | `decode.nodeSelector` | `LLMDBENCH_VLLM_COMMON_AFFINITY` | `vllm_common_affinity` | Node selector for GPU affinity |

        Done. Mapping #2 skipped (would need new env var).
```

## Integration with convert-guide

After running `/convert-guide`, consider running `/refine-mappings review` to:
1. Capture any gaps discovered
2. Improve mappings for future conversions
3. Keep the mapping rules up to date

This creates a continuous improvement loop for the guide converter tool.
