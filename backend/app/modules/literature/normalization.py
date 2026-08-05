"""DOI 与长期论文类型的确定性规范化。"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote

_DOI_PREFIX_PATTERN = re.compile(r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE)
_DOI_TRAILING_PUNCTUATION = ".,;:"
_NON_WORD_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+", re.UNICODE)
_DOCUMENT_TYPE_ALIASES = {
    "article": "journal_article",
    "journalarticle": "journal_article",
    "proceedingsarticle": "conference_paper",
    "conferencepaper": "conference_paper",
    "book": "book",
    "bookchapter": "book_chapter",
    "preprint": "preprint",
    "postedcontent": "posted_content",
    "dataset": "dataset",
    "dissertation": "dissertation",
    "editorial": "editorial",
    "correction": "correction",
    "grant": "grant",
    "peerreview": "peer_review",
    "referenceentry": "reference_entry",
    "retraction": "retraction",
}


def normalize_document_type(value: str | None) -> str | None:
    """将来源类型名称归一为长期论文使用的稳定值。"""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", html.unescape(value))
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip()
    if not normalized:
        return None
    key = _NON_WORD_PATTERN.sub("", normalized.casefold())
    return _DOCUMENT_TYPE_ALIASES.get(key, "other")


def normalize_doi(value: str | None) -> str | None:
    """规范化 DOI 常见前缀、URL 编码和大小写。"""
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    while True:
        without_prefix = _DOI_PREFIX_PATTERN.sub("", normalized).strip()
        if without_prefix == normalized:
            break
        normalized = without_prefix
    normalized = unquote(normalized).strip().rstrip(_DOI_TRAILING_PUNCTUATION).casefold()
    if not re.match(r"^10\.\d{4,9}/\S+$", normalized):
        return None
    return normalized
