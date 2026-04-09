"""定义命令行入口和子命令分发逻辑。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from .env import load_project_dotenv
from .llm import LLMConfig, RelayLLMClient
from .orchestrator.engine import CodeingmeOrchestrator
from .run_paths import create_run_root, spec_case_name
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
            _print_spec_run(bundle, source="cli", case_name=spec_case_name(bundle.spec_dir))
            return 0
        if args and args[0] == "demo":
            case_name = args[1] if len(args) > 1 else "task_service"
            bundle = load_spec_bundle(_default_spec_root() / case_name)
            _print_spec_run(bundle, source="cli", case_name=case_name)
            return 0

        requirement = " ".join(args) if args else "Build a todo backend module with task creation and listing"
        run_root, _ = create_run_root(_repo_root(), source="cli", case_name="adhoc")
        workspace_root = run_root / "workspace"
        spec_dir = run_root / "spec_bundle"
        workspace_root.mkdir(parents=True, exist_ok=True)
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / "requirement.txt").write_text(requirement, encoding="utf-8")
        result = CodeingmeOrchestrator(workspace_root=workspace_root).run(requirement)
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        _write_cli_run_manifest(run_root, {"requirement": requirement}, asdict(result))
        return 0
    finally:
        os.environ.clear()
        os.environ.update(original_env)


def _require_llm_client() -> RelayLLMClient:
    client = RelayLLMClient.from_env()
    if client is None:
        raise RuntimeError(LLMConfig.missing_required_env_message())
    return client


def _print_spec_run(bundle: SpecificationBundle, *, source: str, case_name: str) -> None:
    run_root, _ = create_run_root(_repo_root(), source=source, case_name=case_name)
    workspace_root = run_root / "workspace"
    spec_dir = run_root / "spec_bundle"
    workspace_root.mkdir(parents=True, exist_ok=True)
    _copy_spec_bundle(bundle.spec_dir, spec_dir)
    result = CodeingmeOrchestrator(workspace_root=workspace_root).run(bundle.requirement_prompt())
    payload = asdict(result)
    payload["spec_bundle"] = bundle.to_dict()
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    _write_cli_run_manifest(run_root, bundle.to_dict(), payload)


def _require_argument(args: list[str], command: str) -> str:
    if len(args) < 2:
        raise SystemExit(f"Usage: codeingme {command} <spec-dir>")
    return args[1]


def _default_spec_root() -> Path:
    return Path(__file__).resolve().parents[2] / "specs"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _copy_spec_bundle(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.iterdir()):
        if path.is_file():
            shutil.copy2(path, target_dir / path.name)


def _write_cli_run_manifest(
    run_root: Path,
    bundle_payload: dict[str, object],
    result_payload: dict[str, object],
) -> None:
    manifest = {
        "source": "cli",
        "bundle": bundle_payload,
        "result": result_payload,
        "workspace_root": str(run_root / "workspace"),
        "spec_dir": str(run_root / "spec_bundle"),
    }
    (run_root / "run.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
