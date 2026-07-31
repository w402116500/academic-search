"""集中导入全部数据库模型，供 Alembic 发现表元数据。"""

from app.db.base import Base
from app.db.models.collection import CollectionPaper, ResearchCollection
from app.db.models.document import Document, DocumentChunk, IngestionRun
from app.db.models.paper import Paper
from app.db.models.research import Conversation, Message, ResearchEvidence, ResearchRun
from app.db.models.user import User
from app.db.models.workflow import ResearchPlan, SearchRun

__all__ = [
    "Base",
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
