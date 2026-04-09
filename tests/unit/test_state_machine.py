"""覆盖状态机流转逻辑的单元测试。"""

from __future__ import annotations

from codeingme.orchestrator.state_machine import ExecutionState, StateMachine


def test_state_machine_happy_path() -> None:
    machine = StateMachine()

    machine.transition(ExecutionState.CONTRACT_GENERATION)
    machine.transition(ExecutionState.TEST_RED)
    machine.transition(ExecutionState.IMPLEMENTATION_LOOP)
    machine.transition(ExecutionState.GRAPH_SYNC)
    machine.transition(ExecutionState.CASCADE_UPDATE)
    machine.transition(ExecutionState.VERIFICATION)
    machine.transition(ExecutionState.DONE)

    assert machine.state is ExecutionState.DONE
