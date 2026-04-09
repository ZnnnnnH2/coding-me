# Evaluation Plan

## Demo Cases

The repository currently ships three backend-oriented cases with increasing business complexity:

- `warehouse_dispatch` for simple read-model demos
- `task_service` for medium single-entity workflow demos
- `return_request_service` for hard multi-step workflow demos

These are intentionally staged so the project can be evaluated on progressively richer explicit module tasks rather than jumping directly into the hardest case.

## Metrics

- acceptance test pass rate
- verification pass rate after repair
- regression rate across reruns
- repair iteration count
- amount of manual patching needed after generation

## Baselines

Planned baseline comparisons:

- direct natural-language prompt without structured specs
- structured specs without graph-aware context slicing
- structured specs with test-first orchestration

## Output Artifacts

Evaluation should produce:

- per-case JSON result summaries
- failure and rollback traces
- aggregate metric tables for baseline comparison
