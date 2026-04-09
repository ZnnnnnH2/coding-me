# Task Service

## Complexity Level

Medium

## Business Context

A small operations team uses a shared task service to capture work, assign ownership, and mark items complete without losing a stable backend contract. The service is intentionally small, but it still needs to support both reads and writes in a predictable way.

## Primary Users

- Team lead who creates and prioritizes work
- Individual contributor who completes assigned tasks

## Goals

- Create tasks quickly with basic metadata.
- Browse current work and retrieve a single task detail view.
- Mark tasks as complete in an idempotent way.
- Preserve stable API contracts for automated verification.

## In Scope

- One task aggregate
- Create, list, detail, and complete operations
- Basic validation and client-visible error responses
- Stable completion timestamps

## Out of Scope

- Subtasks or dependency graphs
- Comments and attachments
- User authentication and authorization
- Bulk edits and bulk completion

## Key Scenarios

1. A team lead creates a task with an owner, priority, and optional due date.
2. A contributor opens the task list to see what remains unfinished.
3. A client fetches one task to confirm the latest completion state.
4. Completing an already completed task returns the same final representation instead of creating a duplicate state transition.
5. Completing an unknown task returns a clear client-visible error.

## Success Criteria

- New tasks start incomplete and include a creation timestamp.
- Task list and task detail views always show the latest completion state.
- Unknown task IDs return `404`.
- Invalid create requests return `400` with structured error details.
- Completing the same task twice is idempotent.
