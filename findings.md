# Findings

## Repository
- Root contains `pyproject.toml`, `.venv`, `src/`, `tests/`, `specs/`, `demo_cases/`.
- Project defines console scripts `codeingme` and `codeingme-studio`.
- Declared runtime dependencies: `fastapi`, `httpx`, `socksio`, `uvicorn`.

## Windows Migration Risks
- README quick-start commands still use `source .venv/bin/activate`, which is WSL/Linux specific.
- Existing `.venv` may be a migrated Linux virtualenv and therefore invalid on Windows.
- Current `.venv/pyvenv.cfg` points to `/home/znnnnnh2/.local/share/uv/python/cpython-3.11-linux-x86_64-gnu/bin`.
- Current `.venv` layout is Linux-style (`bin/`, `lib/`, `lib64`) instead of standard Windows `Scripts/`.
- `.venv/bin/python`, `.venv/bin/python3`, and `.venv/bin/python3.11` are zero-byte files, so the migrated interpreter shims are not usable on Windows.
- `uv run ...` on Windows automatically removed the migrated Linux `.venv` and recreated a native Windows `.venv` using CPython 3.11.14.
- After recreation, the project can execute through `uv run` successfully.

## Code Entry Points
- Package exposes both module entry (`python -m codeingme`) and console scripts (`codeingme`, `codeingme-studio`).
- CLI routes `studio`, `llm-models`, `llm-test`, `spec-summary`, `run-spec`, and `demo` subcommands through `src/codeingme/cli.py`.
- `uv run codeingme spec-summary specs\warehouse_dispatch` succeeds on Windows, so the generated console script for `codeingme` works.
- `uv run python -m codeingme studio --help` succeeds and prints argparse help.
- `uv run codeingme-studio --help` does not exit and instead behaves like a server launch, because `src/codeingme/studio.py` parses `argv or []` rather than `sys.argv[1:]` when called as a console script entry point.

## Test and Runtime Validation
- `uv run pytest -q` failed with `PermissionError: [WinError 5]` while pytest tried to access `C:\Users\znnnnnh2\AppData\Local\Temp\pytest-of-znnnnnh2`.
- The failure is environmental rather than application-code-level; rerunning as `uv run pytest -q --basetemp .pytest-tmp` passes with `74 passed`.
- Attempting to inspect that temp directory ACL from PowerShell also returned `Attempted to perform an unauthorized operation`, which supports a Windows temp-directory permission issue.
- `uv run python -m codeingme spec-summary specs\warehouse_dispatch` succeeds and returns correct bundle metadata.
- Direct orchestrator execution with `CODEINGME_ENABLE_LLM=0` reaches final state `done` and finishes verification successfully on Windows.
- `python -m codeingme demo warehouse_dispatch` timed out when launched through the CLI because `.env` is loaded with override semantics and re-enables configured LLM behavior.
- After adding `addopts = "--basetemp=.pytest-tmp"` to pytest config, plain `uv run pytest -q` now passes on Windows with `75 passed`.

## Behavior Notes
- `src/codeingme/env.py` loads `.env` with `override=True`, so repository `.env` values take precedence over shell-provided overrides.
- `src/codeingme/studio.py` now uses `parser.parse_args(argv)` so the console script respects real process arguments when `argv` is omitted.
- Added a regression test that verifies `studio.main()` reads `sys.argv` correctly for console-script invocation.
- README quick start now uses `uv sync` and `uv run ...`, which is platform-neutral across Windows and WSL/Linux.
- `.gitignore` now ignores `.pytest-tmp/` so the repo-local pytest temp directory does not dirty the worktree.

## LLM Relay Compatibility
- `src/codeingme/llm/client.py` already sends a `Responses` request that matches the documented fields: `model`, `instructions`, `input`, `temperature`, `reasoning.effort`, and `max_output_tokens`.
- The current relay at `https://9985678.xyz/v1` can return `HTTP 200` for `/responses` with `status="completed"` and non-zero usage while still returning `output: []`, which conflicts with the documented `Responses` shape.
- `D:\\Codes\\chatme` uses `/chat/completions` rather than `/responses`, and its payload shape differs from `codeingme`: `reasoning_effort`, `max_completion_tokens`, and optional streaming.
- `codeingme` now tries LLM completions in this order: `/responses` non-stream, `/responses` stream, `/chat/completions` non-stream, `/chat/completions` stream.
- The new `/responses` streaming parser recognizes SSE events and extracts text from `response.output_text.delta`, `response.output_text.done`, and final embedded `response` payloads.
- `codeingme` keeps `/responses` as the first attempt, then falls back to a `chat/completions` payload aligned with `chatme`, and finally to a streaming `chat/completions` read path when non-stream responses omit visible text.
- The client now records fallback diagnostics on `LLMCompletion.raw["_codeingme"]`, including endpoint, whether the winning path was streamed, and earlier fallback errors.
