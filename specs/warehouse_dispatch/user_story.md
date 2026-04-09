# Warehouse Dispatch Queue

## Complexity Level

Simple

## Business Context

Warehouse dispatch coordinators need a read-only queue view for outbound work. During shift handoff, the next coordinator must be able to see both unfinished work and the items that were just completed so the handoff conversation stays grounded in one shared queue.

## Primary Users

- Warehouse dispatch coordinator
- Shift supervisor taking over the next shift

## Goals

- View the current outbound dispatch queue in one stable response.
- Keep completed work visible long enough for shift handoff review.
- See the dock, priority, and planned departure time for each queue item.

## In Scope

- One backend read model for outbound dispatch tasks
- One primary API endpoint for queue retrieval
- Stable response payload for automated verification
- Summary counts for open and completed work

## Out of Scope

- Creating dispatch tasks
- Editing or reassigning dispatch work
- Real-time push notifications
- Labor planning and staffing

## Key Scenarios

1. A coordinator opens the queue before loading begins and reviews all pending dispatch tasks.
2. A recently completed task remains visible so the next shift can verify that the step was already handled.
3. A supervisor checks the queue summary to understand how much outbound work is still open.

## Success Criteria

- The list endpoint always returns a predictable object payload.
- Every queue item exposes whether it is completed.
- Completed tasks remain visible during shift handoff review.
- The queue is ordered so urgent and earlier departures appear first.
