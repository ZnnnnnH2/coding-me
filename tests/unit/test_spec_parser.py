"""覆盖规格解析逻辑的单元测试。"""

from __future__ import annotations

from pathlib import Path

from codeingme.spec_parser import load_spec_bundle


def test_load_spec_bundle_extracts_summary_endpoints_and_rules() -> None:
    spec_dir = Path(__file__).resolve().parents[2] / "specs" / "task_service"

    bundle = load_spec_bundle(spec_dir)

    assert bundle.service_name == "task_service"
    assert bundle.summary == "Manage task records with completion state and contract-focused verification."
    assert bundle.endpoints == ["/api/tasks", "/api/tasks/{task_id}/complete"]
    assert bundle.tables == ["tasks"]
    assert bundle.rules == [
        "Tasks must expose their completion state through the API response.",
        "Completing an unknown task must return a client-visible error.",
        "Task listing must remain available after completion updates.",
    ]
    assert "task service backend module" in bundle.requirement_prompt()
