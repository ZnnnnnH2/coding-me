# Architecture

`codeingme` is being repositioned from a general "web app agent" demo into a research prototype for specification-driven backend module generation.

## Current Runtime Layers

- `specs/` provides structured inputs for each backend module case.
- `src/codeingme/spec_parser/` loads a spec bundle and summarizes endpoints, tables, and business rules.
- `src/codeingme/orchestrator/` drives the implementation loop with explicit execution states.
- `src/codeingme/contracts/` holds the lightweight requirement and test abstractions already used by the orchestrator.
- `src/codeingme/graph/` stores requirement, schema, API, and runtime nodes and supports cascade planning.
- `src/codeingme/ast_pipeline/` synchronizes generated Python source back into graph nodes.
- `src/codeingme/runtime/` applies patches, runs tests, and restores checkpoints on failure.

## Research Mapping

The present codebase already contains most of the control-plane pieces needed for a research prototype:

- state transitions support controlled generation rather than free-form retries
- graph and AST layers support traceability between requirements, contracts, and generated code
- layered tests support red-green verification and repair evaluation

The current implementation does not yet have a full typed `testgen/` or `repair/` package. Instead, those concerns are partially embedded in the orchestrator, QA agent, graph sync, and rollback flow. The new repository structure makes those seams explicit so they can be evolved into first-class research modules.

## Compatibility Note

Generated artifacts are now grouped per run under `.codeingme/runs/<source>/<case>/<run_id>/workspace/`. Each run still uses a workspace-local `demo_app/` directory because the orchestrator, tests, and graph sync logic rely on those paths today. The top-level `specs/` directory is the research-facing entry point; `demo_app/` remains the runtime output target within each run workspace.
