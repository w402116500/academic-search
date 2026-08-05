"""Document-owned Redis key contracts for short-lived full-text state."""

from __future__ import annotations

from uuid import UUID

_SEARCH_SESSION_KEY_PREFIX = "academic-search:search-run"


def _validated_search_session_key(session_key: str, purpose: str) -> str:
    if not session_key.startswith(f"{_SEARCH_SESSION_KEY_PREFIX}:"):
        raise ValueError(f"{purpose}必须位于服务端生成的检索会话键下")
    return session_key


def build_candidate_fulltext_key(session_key: str, candidate_id: UUID) -> str:
    """Return the stable full-text state key for one candidate."""
    session_key = _validated_search_session_key(session_key, "全文状态")
    return f"{session_key}:candidate:{candidate_id}:fulltext"


def build_candidate_fulltext_upload_lock_key(session_key: str, candidate_id: UUID) -> str:
    """Return the stable upload lock key for one candidate."""
    session_key = _validated_search_session_key(session_key, "候选上传锁")
    return f"{session_key}:candidate:{candidate_id}:fulltext-upload-lock"
