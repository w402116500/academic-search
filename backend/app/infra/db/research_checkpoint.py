"""LangGraph PostgreSQL checkpoint adapter。"""

from __future__ import annotations

from typing import cast

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import StateGraph

from app.modules.agents.checkpoint import ResearchGraphExecutor
from app.modules.agents.state import SingleRagState


class PostgresResearchGraphExecutor(ResearchGraphExecutor):
    """为每次研究运行管理 PostgreSQL saver 生命周期。"""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def invoke(
        self,
        graph: StateGraph,
        initial_state: SingleRagState,
        thread_id: str,
    ) -> SingleRagState:
        config = cast(RunnableConfig, {"configurable": {"thread_id": thread_id}})
        async with AsyncPostgresSaver.from_conn_string(self._database_url) as checkpointer:
            await checkpointer.setup()
            compiled = graph.compile(checkpointer=checkpointer)
            return cast(SingleRagState, await compiled.ainvoke(initial_state, config=config))
