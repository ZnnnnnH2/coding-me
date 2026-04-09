"""覆盖规格解析逻辑的单元测试。"""

from __future__ import annotations

from pathlib import Path

from codeingme.spec_parser import load_spec_bundle


def test_load_spec_bundle_extracts_summary_endpoints_and_rules() -> None:
    spec_dir = Path(__file__).resolve().parents[2] / "specs" / "task_service"

    bundle = load_spec_bundle(spec_dir)

    assert bundle.service_name == "task_service"
    assert bundle.summary == "Manage team tasks with stable read and completion contracts."
    assert bundle.endpoints == ["/api/tasks", "/api/tasks/{task_id}", "/api/tasks/{task_id}/complete"]
    assert bundle.tables == ["tasks"]
    assert bundle.rules == [
        "`title` is required after trimming and must be between 3 and 120 characters.",
        "`owner` is required for task creation.",
        "`priority` must be one of `low`, `normal`, or `high`.",
        "Newly created tasks must start with `completed = false` and `completed_at = null`.",
        "Completing an unknown task must return a client-visible `404` error.",
        "Completing an already completed task must return `200` and preserve the original `completed_at` timestamp.",
        "Task list and task detail endpoints must both expose the latest `completed` and `completed_at` values.",
        "Task listing must remain available after completion updates.",
        "Open tasks should appear before completed tasks in the default list response.",
        "Within the same completion group, tasks should be ordered by `due_date` ascending and then `created_at` ascending.",
    ]
    assert "task service backend module" in bundle.requirement_prompt()
