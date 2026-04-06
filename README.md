# codeingme

A research prototype for specification-driven, test-first backend module generation.

面向规格驱动与测试先行的后端业务模块自动生成研究原型。

## Motivation

Current coding agents can generate code, run tests, and iterate on failures, but reliable backend module delivery remains difficult. This repository narrows the scope to a clearer research problem: explicit specifications, layered tests, controlled orchestration, and repair loops that can be measured.

## Research Question

In specification-explicit backend development scenarios, can test-first generation and graph-aware repair improve correctness and reduce regression compared with direct prompt-based generation?

## Key Ideas

1. Specification-driven input instead of free-form prompting
2. Test-first orchestration instead of code-first generation
3. State-machine-controlled workflow instead of unconstrained retries
4. Graph-aware repair instead of blind patch loops
5. Layered evaluation with unit, integration, and end-to-end tests

## Pipeline

Specification
-> Spec Parsing
-> Candidate Test Generation
-> State-Machine Orchestration
-> Verification
-> Graph-Aware Repair
-> Accepted Backend Module

## Quick Start

Run the built-in task service demo from a structured spec bundle:

```bash
source .venv/bin/activate
python -m codeingme demo task_service
```

Run directly from a spec directory:

```bash
source .venv/bin/activate
python -m codeingme run-spec specs/task_service
```

Inspect the parsed specification summary:

```bash
source .venv/bin/activate
python -m codeingme spec-summary specs/task_service
```

The current prototype still writes generated runtime artifacts into a workspace-local `demo_app/` directory for compatibility with the existing orchestrator and tests.

## Structured Inputs

Specification bundles live under `specs/` and currently accept:

- `openapi.yaml`
- `schema.sql`
- `business_rules.yaml`
- `user_story.md`

Included demo bundles:

- `specs/task_service/`
- `specs/order_service/`

## Repository Structure

- `src/codeingme/spec_parser/`: lightweight specification bundle loader
- `src/codeingme/orchestrator/`: state-machine workflow control
- `src/codeingme/graph/`: graph store, slice building, and cascade planning support
- `src/codeingme/ast_pipeline/`: AST synchronization utilities
- `src/codeingme/contracts/`: internal requirement and test contracts
- `src/codeingme/runtime/`: patch application, execution, and rollback helpers
- `specs/`: structured specification inputs for backend module cases
- `demo_cases/`: scenario descriptions for the research demo cases
- `docs/`: architecture, methodology, and evaluation notes
- `tests/`: unit, integration, and e2e coverage

## Evaluation

The prototype is intended to be evaluated with:

- acceptance test pass rate
- regression rate after repair
- number of repair iterations
- human patch size after generation
- time and token cost

## Current Status

This repository is still an early-stage research prototype. The current implementation already includes state transitions, graph updates, AST synchronization, rollback, and layered tests. The newly added `specs/` and `spec_parser/` layers reframe those mechanics around a specification-driven backend-module workflow.

## Roadmap

- [ ] extend `spec_parser` from lightweight parsing to richer typed contracts
- [ ] add dedicated `testgen/` modules for acceptance and integration test drafting
- [ ] turn graph slices into targeted repair context selection
- [ ] add baseline comparison scripts for direct prompt generation
- [ ] report regression and repair metrics across multiple backend cases

## LLM Relay

The relay client targets `https://9985678.xyz/v1` by default.

```bash
export OPENAI_API_KEY=...
export CODEINGME_LLM_MODEL=gpt-5.4
export CODEINGME_ENABLE_LLM=1
```

If your machine needs the local proxy from environment variables:

```bash
export CODEINGME_LLM_TRUST_ENV=1
```

Connectivity check:

```bash
codeingme llm-test "Reply with OK only."
```
