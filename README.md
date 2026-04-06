# codeingme

A Python-first prototype of a state-machine-driven Web App Agent framework.

## Run

```bash
source .venv/bin/activate
codeingme "Build a tasks web app with listing and completion state"
```

Or:

```bash
source .venv/bin/activate
python -m codeingme "Build a tasks web app with listing and completion state"
```

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
