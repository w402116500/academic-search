"""受控全文获取、校验与暂存能力。"""

from app.modules.fulltext.acquisition import OpenAccessPdfAcquirer
from app.modules.fulltext.contracts import (
    AcquiredFulltext,
    FulltextAcquisitionError,
    FulltextAcquisitionErrorCode,
    FulltextAcquisitionResult,
    FulltextAcquisitionStatus,
)
from app.modules.fulltext.settings import (
    FulltextAcquisitionSettings,
    get_fulltext_acquisition_settings,
)
from app.modules.fulltext.storage import Boto3StagingObjectStorage, FulltextStorageError

__all__ = [
    "AcquiredFulltext",
    "Boto3StagingObjectStorage",
    "FulltextAcquisitionError",
    "FulltextAcquisitionErrorCode",
    "FulltextAcquisitionResult",
    "FulltextAcquisitionSettings",
    "FulltextAcquisitionStatus",
    "FulltextStorageError",
    "OpenAccessPdfAcquirer",
    "get_fulltext_acquisition_settings",
]
