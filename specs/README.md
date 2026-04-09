# Demo Spec Bundles

The `specs/` directory contains three progressively harder demo bundles. They are intended to be used at different stages of the project rather than as interchangeable examples.

## Recommended Progression

### 1. Simple: `warehouse_dispatch`

- Goal: demonstrate a business-flavored read model with a stable response contract.
- Why it is simple: one table, one primary endpoint, and no write-side workflow.
- Best time to use it: early parser demos, prompt-shaping checks, and smoke tests.
- Core behaviors: queue listing, completion visibility, and stable ordering for shift handoff.

### 2. Medium: `task_service`

- Goal: demonstrate a complete single-entity workflow with both reads and writes.
- Why it is medium: one entity, but multiple operations, validation rules, detail retrieval, and idempotent completion updates.
- Best time to use it: mid-stage contract generation, mutation handling, and backend verification demos.
- Core behaviors: create, list, fetch detail, complete, and return structured client errors.

### 3. Hard: `return_request_service`

- Goal: demonstrate a multi-step business process with richer constraints and status transitions.
- Why it is hard: two related tables, aggregate detail views, approval and warehouse receipt steps, and stronger consistency rules.
- Best time to use it: late-stage end-to-end demos, workflow reasoning experiments, and richer acceptance-test evaluation.
- Core behaviors: request creation, review decision, receipt confirmation, closure gating, and aggregate quantity consistency.

## Authoring Standard

All three bundles follow the same documentation standard:

- `user_story.md` explains business context, actors, scope, key scenarios, and non-goals.
- `openapi.yaml` defines request and response contracts, error shapes, and examples.
- `schema.sql` defines the persistence model with keys, checks, and supporting indexes.
- `business_rules.yaml` groups testable rules by concern.
