"""文献候选的确定性字段规整与匹配键生成函数。"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

from app.modules.literature.normalization import normalize_document_type, normalize_doi
from app.modules.search.contracts import CandidateAuthor, CandidateLanguage, RawCandidate

_NON_WORD_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+", re.UNICODE)
_CHINESE_CHARACTER_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_CHARACTER_PATTERN = re.compile(r"[A-Za-z]")
_CHINESE_LANGUAGE_ALIASES = frozenset({"zh", "zh-cn", "zh-hans", "chi", "zho", "chinese"})
_ENGLISH_LANGUAGE_ALIASES = frozenset({"en", "en-us", "en-gb", "eng", "english"})


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


def normalize_candidate_language(value: CandidateLanguage | str | None) -> CandidateLanguage | None:
    """将来源语言码收敛为候选语言分类，未知来源值不错误归为英文。"""
    if value is None:
        return None

    if isinstance(value, CandidateLanguage):
        return value

    normalized = normalize_text(value).casefold().replace("_", "-")

    if not normalized:
        return None
    if normalized in _CHINESE_LANGUAGE_ALIASES:
        return CandidateLanguage.CHINESE
    if normalized in _ENGLISH_LANGUAGE_ALIASES:
        return CandidateLanguage.ENGLISH
    return CandidateLanguage.OTHER


def infer_candidate_language(title: str, abstract: str | None = None) -> CandidateLanguage:
    """在来源未给语言时，以标题优先的保守规则给出展示分类。

    标题比摘要短且更接近用户在候选列表中看到的文本，因此中文标题即使附带英文
    摘要，也会稳定归为中文文献。无法从标题判断时才回退摘要。
    """
    for text in (title, abstract):
        if not text:
            continue
        if _CHINESE_CHARACTER_PATTERN.search(text):
            return CandidateLanguage.CHINESE
        if _LATIN_CHARACTER_PATTERN.search(text):
            return CandidateLanguage.ENGLISH

    return CandidateLanguage.UNKNOWN


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
            "language": normalize_candidate_language(candidate.language),
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
