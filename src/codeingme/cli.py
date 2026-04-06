from __future__ import annotations

import json
import sys
from dataclasses import asdict

from .llm import RelayLLMClient
from .orchestrator.engine import CodeingmeOrchestrator


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "studio":
        from .studio import main as studio_main

        return studio_main(args[1:])
    if args and args[0] == "llm-models":
        client = _require_llm_client()
        try:
            print(json.dumps({"models": client.list_models()}, indent=2, ensure_ascii=False))
        finally:
            client.close()
        return 0
    if args and args[0] == "llm-test":
        client = _require_llm_client()
        prompt = " ".join(args[1:]) if len(args) > 1 else "Reply with OK and one short sentence about the current model."
        try:
            completion = client.prompt(
                "You are a model connectivity probe. Keep the answer concise.",
                prompt,
                max_tokens=120,
            )
            print(
                json.dumps(
                    {
                        "base_url": client.config.base_url,
                        "model": completion.model,
                        "content": completion.content,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        finally:
            client.close()
        return 0

    requirement = " ".join(args) if args else "Build a todo web app with task creation and listing"
    result = CodeingmeOrchestrator().run(requirement)
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    return 0


def _require_llm_client() -> RelayLLMClient:
    client = RelayLLMClient.from_env()
    if client is None:
        raise RuntimeError(
            "Missing LLM credentials. Set CODEINGME_LLM_API_KEY or OPENAI_API_KEY first."
        )
    return client
