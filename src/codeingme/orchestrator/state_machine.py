"""定义编排流程的状态机。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ExecutionState(StrEnum):
    INTAKE = "intake"
    CONTRACT_GENERATION = "contract_generation"
    TEST_RED = "test_red"
    IMPLEMENTATION_LOOP = "implementation_loop"
    GRAPH_SYNC = "graph_sync"
    CASCADE_UPDATE = "cascade_update"
    VERIFICATION = "verification"
    DONE = "done"
    ROLLBACK = "rollback"


@dataclass(slots=True)
class StateMachine:
    state: ExecutionState = ExecutionState.INTAKE
    history: list[ExecutionState] = field(default_factory=list)

    _transitions = {
        ExecutionState.INTAKE: {ExecutionState.CONTRACT_GENERATION},
        ExecutionState.CONTRACT_GENERATION: {ExecutionState.TEST_RED},
        ExecutionState.TEST_RED: {ExecutionState.IMPLEMENTATION_LOOP},
        ExecutionState.IMPLEMENTATION_LOOP: {ExecutionState.GRAPH_SYNC, ExecutionState.ROLLBACK},
        ExecutionState.GRAPH_SYNC: {ExecutionState.CASCADE_UPDATE},
        ExecutionState.CASCADE_UPDATE: {ExecutionState.VERIFICATION},
        ExecutionState.VERIFICATION: {ExecutionState.DONE, ExecutionState.ROLLBACK},
        ExecutionState.ROLLBACK: {ExecutionState.IMPLEMENTATION_LOOP},
        ExecutionState.DONE: set(),
    }

    def transition(self, new_state: ExecutionState) -> None:
        allowed = self._transitions[self.state]
        if new_state not in allowed:
            raise ValueError(f"Invalid transition {self.state} -> {new_state}")
        self.history.append(self.state)
        self.state = new_state
