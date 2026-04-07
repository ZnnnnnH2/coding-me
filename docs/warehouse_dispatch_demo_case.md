# Warehouse Dispatch Demo Case

## 1. Case Positioning

This case is a small, defense-ready demonstration of the repository goal:

> automatically generate a reliable backend business module from structured requirement documents, API specifications, and database schema.

The chosen scenario is a warehouse dispatch queue. It is intentionally small enough to run end-to-end in the current prototype, while still looking like a business module instead of a generic todo example.

This case demonstrates that the system can:

- accept a structured specification bundle
- summarize the bundle into a controlled requirement prompt
- generate verification tests before implementation
- generate backend and runtime artifacts
- synchronize generated code back into the graph model
- run verification and finish in a `done` state

## 2. Why This Example Was Chosen

The current prototype is strongest on list-oriented business modules with explicit visibility rules. The warehouse dispatch queue fits that boundary well:

- it has a clear backend read model
- it has explicit API and schema inputs
- it has business rules about completion visibility
- it can be verified with stable acceptance tests

At the same time, it avoids overstating the current system capability. This prototype is not yet a full general-purpose backend generator for complex multi-step transactional domains.

## 3. Structured Inputs

The demo bundle is stored under:

- [user_story.md](/home/znnnnnh2/Codes/codeingme/specs/warehouse_dispatch/user_story.md)
- [openapi.yaml](/home/znnnnnh2/Codes/codeingme/specs/warehouse_dispatch/openapi.yaml)
- [schema.sql](/home/znnnnnh2/Codes/codeingme/specs/warehouse_dispatch/schema.sql)
- [business_rules.yaml](/home/znnnnnh2/Codes/codeingme/specs/warehouse_dispatch/business_rules.yaml)

### 3.1 User Story

The requirement describes a backend module for warehouse dispatch coordinators to:

- review the outbound work queue
- keep completion visibility stable during shift handoff
- expose a predictable read model for verification

### 3.2 API Specification

The API contract defines one business endpoint:

- `GET /api/warehouse-dispatch-tasks`

Its purpose is to return the current dispatch queue in a stable, testable format.

### 3.3 Database Schema

The schema defines one table:

- `warehouse_dispatch_tasks`

Fields:

- `id`
- `title`
- `completed`

### 3.4 Business Rules

The rule file makes the business constraint explicit:

- completion state must be visible in the API response
- completed dispatch tasks must remain visible for shift handoff review
- the queue endpoint must stay readable even when some tasks are already complete

## 4. What The System Parsed From The Bundle

Running:

```bash
source .venv/bin/activate
python -m codeingme spec-summary specs/warehouse_dispatch
```

produced a structured summary with these key fields:

- `service_name = warehouse_dispatch_queue`
- `endpoints = ["/api/warehouse-dispatch-tasks"]`
- `tables = ["warehouse_dispatch_tasks"]`
- `summary = "List warehouse dispatch tasks with visible completion state for shift handoff."`

This shows that the prototype can already extract a usable requirement summary from the structured files without depending on a free-form natural-language prompt alone.

## 5. End-to-End Execution

The demo was executed with:

```bash
source .venv/bin/activate
python -m codeingme run-spec specs/warehouse_dispatch
```

The orchestration completed successfully with:

- `final_state = done`
- verification result: `2 passed`

The observed state path was:

1. `intake`
2. `contract_generation`
3. `test_red`
4. `implementation_loop`
5. `graph_sync`
6. `cascade_update`
7. `verification`
8. `done`

## 6. What Was Generated

The run workspace was:

- [demo_workspace](/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace)

Key generated artifacts:

- [warehouse_dispatch_tasks_api.py](/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace/demo_app/warehouse_dispatch_tasks_api.py)
- [test_warehouse_dispatch_tasks_demo.py](/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace/tests_generated/test_warehouse_dispatch_tasks_demo.py)
- [graph.json](/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace/graph.json)
- [Dockerfile](/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace/Dockerfile)
- [docker-compose.yml](/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace/docker-compose.yml)

### 6.1 Generated Backend

The backend generator produced:

- FastAPI app title: `Warehouse Dispatch Task Board`
- service class: `WarehouseDispatchTaskService`
- route: `GET /api/warehouse-dispatch-tasks`
- response shape: `{"warehouse_dispatch_tasks": [...]}` with `id`, `title`, and `completed`

This confirms that the generated module was domain-shaped by the input bundle rather than staying on the default `/api/tasks` demo path.

### 6.2 Generated Acceptance Tests

The generated test file verifies:

- the API contract shape
- that completed and open tasks both remain visible in the API payload

This is important for the research framing: the system enters a red-test phase first, then generates implementation to satisfy those tests.

## 7. Why This Demo Is Meaningful

This case is useful in a defense because it shows the whole pipeline, not just code generation.

What it proves:

- the input is structured
- the orchestration is state-machine controlled
- testing is generated before implementation
- verification is automated
- graph sync and cascade planning are part of the execution model

In other words, the value of the system is not only that it writes a FastAPI file. The value is that it turns structured specifications into a traceable, verifiable, and repair-aware backend module workflow.

## 8. Recommended Defense Walkthrough

A clean live or slide-based walkthrough can follow this order:

1. Show the four structured inputs in `specs/warehouse_dispatch/`.
2. Explain that they correspond to requirement, API contract, schema, and business rules.
3. Run `python -m codeingme spec-summary specs/warehouse_dispatch`.
4. Run `python -m codeingme run-spec specs/warehouse_dispatch`.
5. Show the final `done` state and verification success.
6. Open the generated backend file and point out the generated route and service class.
7. Open the generated test file and explain that the system wrote tests before implementation.
8. Open `graph.json` and explain that generated code is synchronized into a dependency graph for later repair and impact analysis.

## 9. Suggested Oral Explanation

You can describe the case in one short paragraph like this:

> This demo starts from four structured artifacts: a user story, an OpenAPI contract, a SQL schema, and explicit business rules. The system parses them into a controlled requirement summary, generates acceptance tests first, then generates the backend module and related runtime artifacts, synchronizes the generated code into a graph model, and finally runs verification. In this example, the generated module is a warehouse dispatch queue backend with a domain-specific API route and passing verification tests.

## 10. Current Prototype Limits

To keep the defense technically honest, the following limits should be stated clearly:

- the current prototype is strongest on small list-oriented backend modules
- the implementation is still template-heavy in several places
- single-collection flows are more mature than richer multi-entity transactional domains

These limits do not invalidate the demo. They simply define the present scope of the prototype.

## 11. Conclusion

The warehouse dispatch case is a good small demonstration because it is:

- business-flavored rather than toy-like
- aligned with the current prototype capability
- fully reproducible from structured inputs
- able to show the full pipeline from specification to verified generated module

For a teacher or defense committee, this example communicates the core contribution clearly:

**the project is not just generating code; it is exploring a specification-driven, test-first, graph-aware workflow for reliable backend module generation.**
