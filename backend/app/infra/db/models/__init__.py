"""集中导入全部数据库模型，供 Alembic 发现表元数据。"""

from app.infra.db.base import Base
from app.infra.db.models.collection import (
    CollectionBibliographyEntry,
    CollectionPaper,
    ResearchCollection,
)
from app.infra.db.models.document import Document, DocumentChunk, IngestionRun
from app.infra.db.models.paper import Paper
from app.infra.db.models.research import Conversation, Message, ResearchEvidence, ResearchRun
from app.infra.db.models.user import User
from app.infra.db.models.workflow import ResearchPlan, SearchRun

__all__ = [
    "Base",
    "CollectionBibliographyEntry",
    "CollectionPaper",
    "Conversation",
    "Document",
    "DocumentChunk",
    "IngestionRun",
    "Message",
    "Paper",
    "ResearchCollection",
    "ResearchEvidence",
    "ResearchPlan",
    "ResearchRun",
    "SearchRun",
    "User",
]
