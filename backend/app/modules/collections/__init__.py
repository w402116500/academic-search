"""研究集合中的已验证文献入库能力。"""

from app.modules.collections.contracts import (
    CollectionAdmissionError,
    CollectionAdmissionErrorCode,
    CollectionAdmissionResult,
    CollectionAdmissionStatus,
)
from app.modules.collections.service import ResearchCollectionAdmissionService

__all__ = [
    "CollectionAdmissionError",
    "CollectionAdmissionErrorCode",
    "CollectionAdmissionResult",
    "CollectionAdmissionStatus",
    "ResearchCollectionAdmissionService",
]
