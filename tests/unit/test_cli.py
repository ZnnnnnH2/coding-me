from __future__ import annotations

import json
from pathlib import Path

from codeingme.cli import main
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
