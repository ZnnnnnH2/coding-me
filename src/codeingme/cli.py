"""定义命令行入口和子命令分发逻辑。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .env import load_project_dotenv
from .llm import LLMConfig, RelayLLMClient
from .orchestrator.engine import CodeingmeOrchestrator
from .spec_parser import SpecificationBundle, load_spec_bundle


def main(argv: list[str] | None = None) -> int:
    original_env = os.environ.copy()
    load_project_dotenv()
    try:
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
                try:
                    completion = client.prompt(
                        "You are a model connectivity probe. Keep the answer concise.",
                        prompt,
                        max_tokens=120,
                    )
                except Exception as exc:
                    print(
                        json.dumps(
                            {
                                "base_url": client.config.base_url,
                                "error": str(exc),
                            },
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                    return 1
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
        if args and args[0] == "spec-summary":
            bundle = load_spec_bundle(_require_argument(args, "spec-summary"))
            print(json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False))
            return 0
        if args and args[0] == "run-spec":
            bundle = load_spec_bundle(_require_argument(args, "run-spec"))
            _print_spec_run(bundle)
            return 0
        if args and args[0] == "demo":
            case_name = args[1] if len(args) > 1 else "task_service"
            bundle = load_spec_bundle(_default_spec_root() / case_name)
            _print_spec_run(bundle)
            return 0

        requirement = " ".join(args) if args else "Build a todo backend module with task creation and listing"
        result = CodeingmeOrchestrator().run(requirement)
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return 0
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def _require_llm_client() -> RelayLLMClient:
    client = RelayLLMClient.from_env()
    if client is None:
        raise RuntimeError(LLMConfig.missing_required_env_message())
    return client


def _print_spec_run(bundle: SpecificationBundle) -> None:
    result = CodeingmeOrchestrator().run(bundle.requirement_prompt())
    payload = asdict(result)
    payload["spec_bundle"] = bundle.to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _require_argument(args: list[str], command: str) -> str:
    if len(args) < 2:
        raise SystemExit(f"Usage: codeingme {command} <spec-dir>")
    return args[1]


def _default_spec_root() -> Path:
    return Path(__file__).resolve().parents[2] / "specs"
