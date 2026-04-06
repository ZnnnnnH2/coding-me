from __future__ import annotations

from dataclasses import dataclass

from codeingme.contracts import TestSpec


@dataclass(slots=True)
class DemoAppBlueprint:
    name: str = "tasks"
    description: str = "A controlled todo-style demo app used to validate the agentic SDLC loop"

    def requirement_prompt(self) -> str:
        return "Build a tasks web app with listing and completion state"

    def acceptance_tests(self) -> list[TestSpec]:
        return [
            TestSpec(
                name="tasks_list_visible",
                description="Task list is rendered from the backend contract",
                expected_state="green",
            ),
            TestSpec(
                name="tasks_completion_visible",
                description="Completed tasks show their completion state",
                expected_state="green",
            ),
        ]
