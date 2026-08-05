"""Research conversation use-case contract consumed by HTTP routers."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from app.modules.research.contracts import (
    AskResearchQuestionResponse,
    ConversationDetailResponse,
    ConversationResponse,
    CreateConversationRequest,
    ResearchRunResponse,
)


class ResearchConversationUseCases(Protocol):
    async def create_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        request: CreateConversationRequest,
    ) -> ConversationResponse: ...

    async def list_conversations(
        self, *, owner_user_id: UUID, collection_id: UUID
    ) -> list[ConversationResponse]: ...

    async def get_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
    ) -> ConversationDetailResponse: ...

    async def ask_question(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        content: str,
        model_config: dict[str, Any],
    ) -> AskResearchQuestionResponse: ...

    async def get_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
    ) -> ResearchRunResponse: ...

    async def retry_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
    ) -> ResearchRunResponse: ...

    async def cancel_run(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
        research_run_id: UUID,
    ) -> ResearchRunResponse: ...

    async def delete_conversation(
        self,
        *,
        owner_user_id: UUID,
        collection_id: UUID,
        conversation_id: UUID,
    ) -> ConversationResponse: ...
