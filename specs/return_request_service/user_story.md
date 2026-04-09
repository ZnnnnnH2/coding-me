# Return Request Service

## Complexity Level

Hard

## Business Context

An after-sales operations team manages customer return requests for completed orders. The service must capture line items, route requests through review, record what the warehouse actually received, and prevent the request from closing until the workflow is complete.

## Primary Users

- Customer support agent creating the request
- Returns reviewer approving or rejecting the request
- Warehouse receiving clerk confirming inbound quantities
- Operations auditor checking the final case history

## Goals

- Create a return request with one or more line items.
- Review the request and either approve or reject it.
- Record how many units were actually received by the warehouse.
- Expose a consistent detail view that combines header and line-item state.
- Prevent illegal status jumps.

## In Scope

- Return request header and item lines
- List, detail, create, review, receive, and close operations
- Status transition validation
- Quantity consistency checks between requested, approved, and received values

## Out of Scope

- Refund payment execution
- Carrier label generation
- Customer identity verification
- Integration with the original order service beyond storing external reference IDs

## Key Scenarios

1. Support creates a return request from a completed order with one or more SKUs.
2. A reviewer approves or rejects the request and records a decision note.
3. Warehouse staff records the quantities actually received after approved items arrive.
4. Operations closes the request only after receipt is complete.
5. Detail retrieval must always expose both header status and per-line quantities.

## Success Criteria

- A request cannot be created without at least one line item.
- Quantities remain consistent across requested, approved, and received stages.
- Only valid status transitions are allowed.
- Rejected requests are terminal.
- Closed requests reflect the latest review and receipt timestamps.
