# Evaluation Plan

## Demo Cases

The repository currently ships two backend-oriented cases:

- `task_service`
- `order_service`

These are intentionally small. The goal is to measure reliability on explicit module tasks before expanding the scope.

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
