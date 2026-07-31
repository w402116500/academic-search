"""研究集合中的已验证文献入库能力。"""

from app.modules.collections.contracts import (
    CollectionAdmissionError,
    CollectionAdmissionErrorCode,
    CollectionAdmissionResult,
    CollectionAdmissionStatus,
)
from app.modules.collections.service import ResearchCollectionAdmissionService
from app.modules.collections.workspace_contracts import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
    WorkflowStageDisplay,
    WorkspaceError,
    WorkspaceErrorCode,
    WorkspaceResponse,
)
from app.modules.collections.workspace_service import ResearchWorkspaceService

__all__ = [
    "CollectionAdmissionError",
    "CollectionAdmissionErrorCode",
    "CollectionAdmissionResult",
    "CollectionAdmissionStatus",
    "CreateWorkspaceRequest",
    "ResearchCollectionAdmissionService",
    "ResearchWorkspaceService",
    "UpdateWorkspaceRequest",
    "WorkspaceError",
    "WorkspaceErrorCode",
    "WorkspaceResponse",
    "WorkflowStageDisplay",
]
