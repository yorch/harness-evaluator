---
title: Multi-phase Evaluation
description: Chain implementation and adversarial review models in a single task with per-phase cost attribution.
---

# Multi-phase Evaluation

Multi-phase evaluation lets you chain multiple harness invocations in a single task, with different models assigned to different phases. The most common pattern is **adversarial review**: an implementation model produces a fix, a more capable reviewer model critiques the diff, and the implementation model revises based on the feedback.

## When to use it

- **Adversarial review**: A cheaper/faster model implements, a more expensive model reviews. Does the review improve quality enough to justify the cost?
- **Iterative refinement**: Implement → review → revise → review → revise. Does a second revision pass improve results?
- **Self-correction**: The same model reviews its own work. Set the review phase's `model_role: implementation` (instead of `review`) so the implementation model runs it, or list the same model twice with different roles. Does self-review help?

Multi-phase is **not** a replacement for the open-ended LLM judge. The judge evaluates the final output post-hoc; multi-phase review feeds feedback back to the implementer before evaluation.

## How it works

```
 ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
 │  Phase 1    │────►│  Phase 2    │────►│  Phase 3    │────►│ Evaluation  │
 │ implement   │     │ review      │     │ revise      │     │ (SWE tests) │
 │ model: A    │     │ model: B    │     │ model: A    │     │             │
 │ input: none │     │ input: diff │     │ input:      │     │             │
 │             │     │             │     │ review_     │     │             │
 │             │     │             │     │ feedback    │     │             │
 └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

1. **Implement** (model A): The implementation model fixes the bug. The git diff is captured.
2. **Review** (model B): The reviewer model receives the diff and produces feedback.
3. **Revise** (model A): The implementation model receives the feedback and revises.
4. **Evaluate**: Hidden tests run against the final repository state.

All phases run in the **same Docker container** so repository state persists. Each phase gets its own gateway trace ID for per-phase cost attribution.

## Task design

Define a `multi_phase` task with a `phases` list. Each phase has a `name`, `model_role`, `task_prompt`, and optional `input`:

```yaml
tasks:
- id: my-multi-phase-task
  name: Bugfix with adversarial review
  track: multi_phase
  task_prompt: "Fix the bug"  # Required but ignored when phases is set
  test_command: python -m pytest tests/
  test_patch: |
    diff --git a/tests/test_hidden.py ...
  phases:
  - name: implement
    model_role: implementation
    task_prompt: |-
      Fix the off-by-one bug in src/solution.py...
    input: none
    timeout_seconds: 300

  - name: review
    model_role: review
    task_prompt: |-
      You are an adversarial code reviewer. Review the diff for
      correctness, security, and edge cases...
    input: diff
    timeout_seconds: 300

  - name: revise
    model_role: implementation
    task_prompt: |-
      Address the reviewer's feedback. If no issues were found,
      make no changes...
    input: review_feedback
    timeout_seconds: 300
```

See `tasks/multi-phase-bugfix-001.yaml` for a complete example.

### Phase input types

| `input` | What the phase receives |
|---------|-------------------------|
| `none` | Nothing from prior phases. |
| `diff` | Git diff from the prior implementation phase. |
| `output` | Stdout + stderr from the prior phase. |
| `review_feedback` | Stdout + stderr from a prior `review` phase. |

The injected content is appended to the phase's `task_prompt` in a delimited section.

## Run design

Assign `role: implementation` and `role: review` to your models in the run config:

```yaml
models:
  - name: claude-sonnet-5
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    role: implementation

  - name: claude-opus-5
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
    role: review
```

The matrix expands to one cell per `implementation × review` model pair. With 2 implementation models and 1 review model, you get 2 cells per harness per repeat.

See `runs/sample-multi-phase.yaml` for a complete example.

## Per-phase cost attribution

Each phase gets a trace ID of `{cell_id}__phase-{phase.name}`. The gateway captures token usage and cost per trace ID, and the runner saves a breakdown to the `phase_results` SQLite table:

```sql
SELECT phase_name, model, total_cost, input_tokens, output_tokens
FROM phase_results
WHERE cell_id = ?
ORDER BY id ASC;
```

This lets you answer questions like:

- How much did the review phase cost vs. the implementation phase?
- Did the reviewer's token usage justify the quality improvement?
- Would a cheaper reviewer model achieve similar results?

## Common pitfalls

- **Forgetting a review model**: If your task has a `review` phase but no model with `role: review`, `build_matrix()` raises a `ValueError`.
- **Duplicate phase names**: Phase names must be unique within a task (they're used in trace IDs and file paths).
- **Expecting per-phase `test_command`**: Tests run only once, after all phases complete. There is no intermediate test step.
- **`task_prompt` is still required**: Even though it's ignored when `phases` is set, the top-level `task_prompt` field is required by the schema.
- **Container env isolation**: Each phase receives its own API key and base URL via `docker exec --env`. The container starts with a minimal env — no API keys are baked in. This prevents leaking one phase's credentials into another.
- **Pipeline abort**: If any phase exits non-zero, the pipeline stops. Implementation-phase changes are committed before the exit-code check; review phases produce no repo changes. The cell is marked as failed in the results store.
