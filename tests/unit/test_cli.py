from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from codeingme.cli import main
from codeingme.llm import LLMCompletion
from codeingme.orchestrator.engine import OrchestrationResult


def test_spec_summary_command_prints_parsed_bundle(capsys) -> None:
    spec_dir = Path(__file__).resolve().parents[2] / "specs" / "task_service"

    exit_code = main(["spec-summary", str(spec_dir)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["service_name"] == "task_service"
    assert payload["endpoints"] == ["/api/tasks", "/api/tasks/{task_id}/complete"]


def test_demo_command_runs_orchestrator_with_spec_prompt(monkeypatch, capsys) -> None:
    recorded: dict[str, str] = {}

    class _RecordingOrchestrator:
        def run(self, requirement: str) -> OrchestrationResult:
            recorded["requirement"] = requirement
            return OrchestrationResult(
                requirement=requirement,
                final_state="done",
                states=["intake", "done"],
                graph_nodes=[],
                blast_radius=[],
            )

    monkeypatch.setattr("codeingme.cli.CodeingmeOrchestrator", _RecordingOrchestrator)

    exit_code = main(["demo", "task_service"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert "task service backend module" in recorded["requirement"]
    assert payload["spec_bundle"]["service_name"] == "task_service"


def test_main_prefers_project_dotenv_over_shell_env(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=dotenv-key",
                "OPENAI_BASE_URL=https://dotenv.example/v1",
                "CODEINGME_LLM_MODEL=dotenv-model",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://shell.example/v1")
    monkeypatch.setenv("CODEINGME_LLM_MODEL", "shell-model")

    captured: dict[str, str | None] = {}

    class _FakeClient:
        def __init__(self) -> None:
            captured["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")
            captured["OPENAI_BASE_URL"] = os.environ.get("OPENAI_BASE_URL")
            captured["CODEINGME_LLM_MODEL"] = os.environ.get("CODEINGME_LLM_MODEL")
            self.config = SimpleNamespace(base_url=os.environ.get("OPENAI_BASE_URL"))

        def prompt(self, *_args, **_kwargs) -> LLMCompletion:
            return LLMCompletion(model=os.environ["CODEINGME_LLM_MODEL"], content="OK")

        def close(self) -> None:
            return None

    monkeypatch.setattr("codeingme.cli._require_llm_client", lambda: _FakeClient())

    exit_code = main(["llm-test", "Reply with OK only."])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert captured == {
        "OPENAI_API_KEY": "dotenv-key",
        "OPENAI_BASE_URL": "https://dotenv.example/v1",
        "CODEINGME_LLM_MODEL": "dotenv-model",
    }
    assert payload["base_url"] == "https://dotenv.example/v1"
    assert payload["model"] == "dotenv-model"


def test_main_restores_shell_env_after_loading_project_dotenv(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv-key\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "shell-key")

    class _FakeClient:
        config = SimpleNamespace(base_url="https://example.test/v1")

        def prompt(self, *_args, **_kwargs) -> LLMCompletion:
            return LLMCompletion(model="fake-model", content="OK")

        def close(self) -> None:
            return None

    monkeypatch.setattr("codeingme.cli._require_llm_client", lambda: _FakeClient())

    exit_code = main(["llm-test", "Reply with OK only."])

    json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert os.environ["OPENAI_API_KEY"] == "shell-key"


def test_llm_test_returns_structured_error_when_provider_fails(monkeypatch, capsys) -> None:
    class _FailingClient:
        config = SimpleNamespace(base_url="https://example.test/v1")

        def prompt(self, *_args, **_kwargs) -> LLMCompletion:
            raise RuntimeError("provider returned HTTP 200 without usable text")

        def close(self) -> None:
            return None

    monkeypatch.setattr("codeingme.cli._require_llm_client", lambda: _FailingClient())

    exit_code = main(["llm-test", "Reply with OK only."])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "base_url": "https://example.test/v1",
        "error": "provider returned HTTP 200 without usable text",
    }
