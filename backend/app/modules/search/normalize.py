"""文献候选的确定性字段规整与匹配键生成函数。"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

from app.modules.search.contracts import CandidateAuthor, RawCandidate

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


@dataclass(frozen=True, slots=True)
class NormalizedCandidateRecord:
    """一条来源候选及其只用于比较的规范化键。"""

    raw: RawCandidate
    doi_key: str | None
    title_key: str
    first_author_key: str | None


def normalize_text(value: str) -> str:
    """统一解码 HTML 实体、全半角字符与连续空白，供展示字段和匹配键共用。"""
    decoded = html.unescape(value)
    normalized = unicodedata.normalize("NFKC", decoded)
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def normalize_optional_text(value: str | None) -> str | None:
    """规整可选文本字段，并用 None 表达来源提供的空值。"""
    if value is None:
        return None

    normalized = normalize_text(value)
    return normalized or None


def normalize_document_type(value: str | None) -> str | None:
    """将各来源不同的文献类型名称归一为稳定内部枚举值。"""
    normalized = normalize_optional_text(value)

    if normalized is None:
        return None

    key = _NON_WORD_PATTERN.sub("", normalized.casefold())
    return _DOCUMENT_TYPE_ALIASES.get(key, "other")


def normalize_raw_candidate(candidate: RawCandidate) -> RawCandidate:
    """在去重前规整来源候选的展示字段，避免格式噪声进入后续全部阶段。"""
    source_record_id = candidate.source_record_id.strip()
    title = normalize_text(candidate.title)

    if not source_record_id:
        raise ValueError("候选来源记录 ID 规整后不能为空")

    if not title:
        raise ValueError("候选标题规整后不能为空")

    authors: list[CandidateAuthor] = []

    for author in candidate.authors:
        name = normalize_text(author.name)

        # 来源偶尔会返回仅包含空白或 HTML 空实体的作者节点，应视为缺失作者而非无效记录。
        if name:
            authors.append(
                CandidateAuthor(
                    name=name,
                    source_author_id=normalize_optional_text(author.source_author_id),
                )
            )

    return candidate.model_copy(
        update={
            "source_record_id": source_record_id,
            "source_record_url": normalize_optional_text(candidate.source_record_url),
            "title": title,
            "authors": tuple(authors),
            "abstract": normalize_optional_text(candidate.abstract),
            "doi": normalize_doi(candidate.doi),
            "venue": normalize_optional_text(candidate.venue),
            "document_type": normalize_document_type(candidate.document_type),
            "volume": normalize_optional_text(candidate.volume),
            "issue": normalize_optional_text(candidate.issue),
            "pages": normalize_optional_text(candidate.pages),
            "article_number": normalize_optional_text(candidate.article_number),
            "publisher": normalize_optional_text(candidate.publisher),
            "landing_url": normalize_optional_text(candidate.landing_url),
            "open_access_url": normalize_optional_text(candidate.open_access_url),
            "fulltext_url": normalize_optional_text(candidate.fulltext_url),
        }
    )


def normalize_doi(value: str | None) -> str | None:
    """规范化 DOI 的常见前缀、URL 编码和大小写，返回可用于精确去重的键。"""
    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", value).strip()

    # 用户或来源可能同时给出 ``doi: https://doi.org/...``，因此要重复移除前缀。
    while True:
        without_prefix = _DOI_PREFIX_PATTERN.sub("", normalized).strip()

        if without_prefix == normalized:
            break

        normalized = without_prefix

    normalized = unquote(normalized).strip().rstrip(_DOI_TRAILING_PUNCTUATION).casefold()

    # DOI 必须具有注册机构前缀和斜杠；不符合该形态的数据保留在原记录但不参与 DOI 去重。
    if not re.match(r"^10\.\d{4,9}/\S+$", normalized):
        return None

    return normalized


def normalize_title_key(value: str) -> str:
    """将标题转换为跨来源保守匹配键，兼容全半角、大小写和标点差异。"""
    normalized = normalize_text(value).casefold()
    normalized = _NON_WORD_PATTERN.sub(" ", normalized)
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized).strip()

    if not normalized:
        raise ValueError("标题规范化后不能为空")

    return normalized


def normalize_author_key(value: str | None) -> str | None:
    """生成作者匹配键；姓名缺失时返回 None，避免把未知作者误当作同一作者。"""
    if value is None:
        return None

    normalized = normalize_text(value).casefold()
    normalized = _NON_WORD_PATTERN.sub("", normalized)
    return normalized or None


def normalize_candidate_record(candidate: RawCandidate) -> NormalizedCandidateRecord:
    """从已规整来源候选提取 DOI、标题和首位作者的比较键。"""
    first_author_name = candidate.authors[0].name if candidate.authors else None

    return NormalizedCandidateRecord(
        raw=candidate,
        doi_key=normalize_doi(candidate.doi),
        title_key=normalize_title_key(candidate.title),
        first_author_key=normalize_author_key(first_author_name),
    )
