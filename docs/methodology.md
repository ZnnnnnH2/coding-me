# Methodology

## Problem Scope

The target problem is not full-stack app generation. It is specification-explicit backend module delivery under structured constraints:

- API contracts are known up front
- database schema is known up front
- business rules are explicit and testable

## Workflow

1. Load a structured spec bundle from `specs/<case>/`.
2. Summarize endpoints, tables, and rules into a controlled requirement prompt.
3. Enter the state-machine orchestrator.
4. Generate red tests before implementation.
5. Implement the backend and runtime support.
6. Synchronize generated code into the dependency graph.
7. Use cascade planning and targeted verification to validate the result.
8. Roll back on failure and preserve checkpoints for repair.

## Intended Research Contributions

- Show that explicit state transitions can constrain generation into a more inspectable SDLC loop.
- Show that structured specs are a better entry point than open-ended prompts for backend-module generation.
- Show that graph and AST synchronization provide a basis for future test-impact-aware repair.

## Near-Term Extensions

- split spec parsing into richer typed models
- add spec-to-test generation modules
- record repair traces and regression outcomes
- compare against a direct-prompt baseline on the same module cases
