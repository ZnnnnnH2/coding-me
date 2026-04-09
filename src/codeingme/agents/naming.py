"""封装生成计划的命名入口。"""

from __future__ import annotations

from codeingme.agents.base import AgentContext
from codeingme.generation_plan import GenerationPlan, build_generation_plan


def generation_plan(context: AgentContext) -> GenerationPlan:
    return build_generation_plan(context)
