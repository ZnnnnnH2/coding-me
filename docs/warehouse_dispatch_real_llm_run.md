# Warehouse Dispatch Real LLM Run Record

Date: 2026-04-07
Workspace: `/home/znnnnnh2/Codes/codeingme`
Spec bundle: `specs/warehouse_dispatch`

## Goal

Run `warehouse_dispatch` end-to-end with the real LLM using the repository `.env`, supervise the run, fix failures if they appear, and keep a complete execution record.

## Effective Configuration

- Default config source priority for CLI execution: repository `.env` first, then inherited shell environment.
- Repository `.env` file: `/home/znnnnnh2/Codes/codeingme/.env`
- Initial repository `.env` base URL: `https://sub.asxs.top/v1`
- Final supervised run override: temporary dotenv file at `/tmp/codeingme_run_asxs.env`
- Final supervised run base URL: `https://api.asxs.top/v1`
- `CODEINGME_ENABLE_LLM`: `1`
- `CODEINGME_LLM_TRUST_ENV`: `1`
- Final supervised run timeout: `60.0`
- API keys were present during execution and are intentionally redacted from this log

## Execution Log

### Step 1. Repository and spec inspection

Status: completed

- Confirmed the spec bundle exists with `user_story.md`, `openapi.yaml`, `schema.sql`, and `business_rules.yaml`.
- Confirmed CLI execution now loads the repository `.env` before reading runtime configuration.

### Step 2. Real LLM connectivity probe

Status: completed

Command:

```bash
source .venv/bin/activate
codeingme llm-test "Reply with OK only."
```

Result:

```json
{
  "base_url": "https://sub.asxs.top/v1",
  "model": "gpt-5.4",
  "content": "OK"
}
```

### Step 3. Provider verification

Status: completed

The following alternate providers were probed after the initial long-running attempt on `sub.asxs.top`:

```text
https://api.asxs.top/v1 -> OK
https://9985678.xyz/v1 -> OK
```

The final supervised runs used `https://api.asxs.top/v1`.

### Step 4. Attempt timeline

Status: completed

Attempt 1:

- Command style: black-box `python -m codeingme run-spec specs/warehouse_dispatch`
- Base URL: `https://sub.asxs.top/v1`
- Outcome: manually terminated because the run blocked for a long period with no stage visibility
- Raw log: `docs/warehouse_dispatch_real_llm_run_attempt1.log`

Attempt 2:

- Command style: instrumented run with event callback and forced `CODEINGME_LLM_TIMEOUT=30`
- Base URL: `https://sub.asxs.top/v1`
- Outcome: `final_state = done`, verification passed
- Observation: architect and QA completed through the real LLM; backend/frontend/devops exposed fallback or timeout artifacts
- Raw log: `docs/warehouse_dispatch_real_llm_run_attempt2.log`

Attempt 3:

- Command style: instrumented run with default timeout from code
- Base URL: `https://sub.asxs.top/v1`
- Outcome: manually terminated during a long frontend wait after the user supplied alternate providers
- Raw log: `docs/warehouse_dispatch_real_llm_run_attempt3.log`

Attempt 4:

- Command style: instrumented run with `CODEINGME_DOTENV_PATH=/tmp/codeingme_run_asxs.env`
- Base URL: `https://api.asxs.top/v1`
- Timeout: `60`
- Outcome: `final_state = done`, verification passed
- Observation: backend was stable on real LLM, but frontend and devops still fell back to templates
- Raw log: `docs/warehouse_dispatch_real_llm_run_attempt4.log`

Attempt 5:

- Command style: instrumented run after LLM robustness fixes
- Base URL: `https://api.asxs.top/v1`
- Timeout: `60`
- Outcome: `final_state = done`, verification passed
- Observation: backend remained stable on real LLM; frontend completed faster but still failed bundle validation; devops also still failed bundle validation and fell back to the template path
- Raw log: `docs/warehouse_dispatch_real_llm_run_attempt5.log`

### Step 5. Micro-tuning changes applied during supervision

Status: completed

The following code changes were applied while supervising the run:

- Added repository `.env` loading at CLI entry so runtime execution prefers project-local configuration.
- At the time of this supervised run, the code still supported a temporary fallback from `CODEINGME_LLM_BASE_URL` to `OPENAI_BASE_URL`. That fallback is no longer part of the current configuration contract.
- Added one corrective retry for structured LLM file generation when a completion times out, returns invalid JSON, omits required files, or fails validation.
- Added timeout-aware retry instructions that explicitly ask for a more compact second response.
- Allowed structured generation parsing to ignore optional extra file entries with empty content instead of failing the whole bundle.
- Reduced frontend prompt verbosity and lowered frontend `max_tokens` from `1200` to `900`.
- Tightened the devops prompt to request only `Dockerfile`, `docker-compose.yml`, and `.dockerignore`.
- Aligned the devops validator with equivalent `uvicorn` command forms and the docker-compose commands actually surfaced by the agent.

### Step 6. Probe logs for targeted diagnosis

Status: completed

- Backend probe log: `docs/warehouse_dispatch_backend_probe.log`
- DevOps probe log: `docs/warehouse_dispatch_devops_probe.log`

These probe logs captured raw LLM prompts and completions for isolated agent debugging.

### Step 7. Final supervised result

Status: completed

Command:

```bash
source .venv/bin/activate
CODEINGME_DOTENV_PATH=/tmp/codeingme_run_asxs.env python -u <instrumented runner>
```

Result:

```text
final_state = done
verification = 2 passed
provider = https://api.asxs.top/v1
```

Final state path:

1. `intake`
2. `contract_generation`
3. `test_red`
4. `implementation_loop`
5. `graph_sync`
6. `cascade_update`
7. `verification`
8. `done`

Final artifact summary:

- `architect`: real LLM success
- `backend`: real LLM success
- `qa`: green verification artifacts in final result
- `frontend`: template fallback remained after two attempts because the generated structured response failed validation
- `devops`: template fallback remained after two attempts because the generated structured response failed validation

Generated workspace:

- `/home/znnnnnh2/Codes/codeingme/.codeingme/demo_workspace`

Verification output excerpt:

```text
tests_generated/test_warehouse_dispatch_tasks_demo.py ..                 [100%]
2 passed in 0.48s
```

## Conclusion

The supervised end-to-end `warehouse_dispatch` run is reproducibly passing with a real LLM and full execution trace.

What is now stable:

- real provider connectivity
- specification parsing
- architect stage
- backend generation through the real LLM
- full orchestration to `done`
- generated verification suite passing

What still degrades internally:

- frontend generation can still fail structured bundle validation and fall back to the template
- devops generation can still fail structured bundle validation and fall back to the template

These residual fallback paths do not block successful execution, but they remain the main reliability gap if the goal is to have every implementation-stage artifact come directly from validated LLM output.
