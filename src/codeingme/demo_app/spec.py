from __future__ import annotations

from dataclasses import dataclass

from codeingme.contracts import TestSpec


@dataclass(slots=True)
class DemoAppBlueprint:
    name: str = "tasks"
    description: str = "A controlled todo-style backend module used to validate the agentic SDLC loop"

    def requirement_prompt(self) -> str:
        return "Build a tasks backend module with listing and completion state"

    def acceptance_tests(self) -> list[TestSpec]:
        return [
            TestSpec(
                name="tasks_contract_visible",
                description="Task list is returned from the backend contract",
                expected_state="green",
            ),
            TestSpec(
                name="tasks_completion_visible",
                description="Completed tasks remain visible through the API payload",
                expected_state="green",
            ),
        ]
