"""检索候选在 Redis 中的短期会话键约定。

PostgreSQL 的 ``search_runs`` 只保存可恢复的运行状态、计数、错误与该会话键。
标题、摘要、来源原始字段和候选详情属于可再生的短期数据，必须在 Redis TTL
到期后失效，而不能绕过 DOI 和全文准入规则写入长期 ``papers`` 表。
"""

from __future__ import annotations

from uuid import UUID

SEARCH_SESSION_KEY_PREFIX = "academic-search:search-run"


def build_search_session_key(search_run_id: UUID) -> str:
    """为一次检索运行生成唯一 Redis 键，不包含用户可控文本。"""
    return f"{SEARCH_SESSION_KEY_PREFIX}:{search_run_id}"
