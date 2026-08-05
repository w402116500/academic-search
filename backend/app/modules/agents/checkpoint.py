"""研究图执行与 checkpoint 的端口。"""

from __future__ import annotations

from typing import Protocol, cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import StateGraph

from app.modules.agents.state import SingleRagState


class ResearchGraphExecutor(Protocol):
    async def invoke(
        self,
        graph: StateGraph,
        initial_state: SingleRagState,
        thread_id: str,
    ) -> SingleRagState: ...


class DirectResearchGraphExecutor:
    """无持久 checkpoint 的图执行器，仅用于离线测试和显式禁用场景。"""

    async def invoke(
        self,
        graph: StateGraph,
        initial_state: SingleRagState,
        thread_id: str,
    ) -> SingleRagState:
        config = cast(RunnableConfig, {"configurable": {"thread_id": thread_id}})
        compiled = graph.compile()
        return cast(SingleRagState, await compiled.ainvoke(initial_state, config=config))
