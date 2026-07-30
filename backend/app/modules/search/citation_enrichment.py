"""将搜索候选与 DOI 权威题录保守合并。"""

from __future__ import annotations

from typing import Protocol

from app.modules.search.contracts import (
    CandidateAuthor,
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
    DoiCslRecord,
    DoiMetadataResolution,
    SourceName,
    UnifiedCandidate,
)
from app.modules.search.normalize import (
    normalize_author_key,
    normalize_document_type,
    normalize_doi,
    normalize_optional_text,
    normalize_title_key,
)

_DOI_PROVENANCE = "doi_content_negotiation"


class DoiMetadataResolverClient(Protocol):
    """题录补全服务依赖的最小 DOI 解析接口。"""

    async def resolve(self, doi: str) -> DoiMetadataResolution:
        """返回一次 DOI 内容协商的成功记录或明确失败信息。"""
        raise NotImplementedError


class CitationMetadataEnricher:
    """按需补全单个搜索候选的正式题录，不改变候选本身的展示字段。"""

    def __init__(self, resolver: DoiMetadataResolverClient) -> None:
        """通过接口注入 DOI 解析器，使合并逻辑可在离线测试中独立验证。"""
        self._resolver = resolver

    async def enrich(self, candidate: UnifiedCandidate) -> UnifiedCandidate:
        """为候选附加题录状态；失败不会丢弃候选或伪装成已核验。"""
        base_values, provenance = _candidate_metadata_values(candidate)

        if candidate.doi is None:
            citation = _build_metadata(values=base_values, provenance=provenance)
            return candidate.model_copy(update={"citation": citation})

        resolution = await self._resolver.resolve(candidate.doi)

        if resolution.error is not None:
            citation = _build_metadata(
                values=base_values,
                provenance=provenance,
                resolution_error=resolution.error,
            )
            return candidate.model_copy(update={"citation": citation})

        assert resolution.record is not None
        merged_values, merged_provenance, conflicts = _merge_doi_record(
            values=base_values,
            provenance=provenance,
            record=resolution.record,
        )
        citation = _build_metadata(
            values=merged_values,
            provenance=merged_provenance,
            conflicts=conflicts,
        )
        return candidate.model_copy(update={"citation": citation})


def _candidate_metadata_values(
    candidate: UnifiedCandidate,
) -> tuple[dict[str, object], dict[str, str]]:
    """提取候选已有题录字段，并把来源枚举转换为对外稳定字符串。"""
    issued_date = candidate.published_date

    if issued_date is None and candidate.published_year is not None:
        # 年份同样是合法 CSL 日期，只是精度低于来自 DOI 的完整发布日期。
        issued_date = CitationDate(year=candidate.published_year)

    values: dict[str, object] = {
        "authors": tuple(_citation_author(author) for author in candidate.authors),
        "title": candidate.title,
        "document_type": candidate.document_type,
        "issued_date": issued_date,
        "venue": candidate.venue,
        "volume": candidate.volume,
        "issue": candidate.issue,
        "pages": candidate.pages,
        "article_number": candidate.article_number,
        "publisher": candidate.publisher,
        "doi": candidate.doi,
        "url": candidate.links.landing_url,
    }
    provenance: dict[str, str] = {}

    for field_name, value in values.items():
        source_name = _source_name(candidate.field_provenance.get(field_name))

        if value is not None and source_name is not None:
            provenance[field_name] = source_name

    # 年份与落地页使用的字段名称与 CitationMetadata 不同，单独补足其来源信息。
    if issued_date is not None and "issued_date" not in provenance:
        source = candidate.field_provenance.get("published_date") or candidate.field_provenance.get(
            "published_year"
        )
        source_name = _source_name(source)

        if source_name is not None:
            provenance["issued_date"] = source_name

    if values["url"] is not None and "url" not in provenance:
        source_name = _source_name(candidate.field_provenance.get("landing_url"))

        if source_name is not None:
            provenance["url"] = source_name

    return values, provenance


def _citation_author(author: CandidateAuthor) -> CitationAuthor:
    """来源未提供结构化姓/名时保留原样，避免对不同语言姓名做错误拆分。"""
    return CitationAuthor(literal=author.name)


def _source_name(source: SourceName | None) -> str | None:
    """将内部来源枚举转换为题录对外说明使用的稳定字符串。"""
    return source.value if source is not None else None


def _merge_doi_record(
    *,
    values: dict[str, object],
    provenance: dict[str, str],
    record: DoiCslRecord,
) -> tuple[dict[str, object], dict[str, str], dict[str, tuple[str, ...]]]:
    """仅补充空字段，已有字段不一致时记录冲突而不静默覆盖。"""
    resolver_values: dict[str, object] = {
        "authors": record.authors,
        "title": record.title,
        "document_type": normalize_document_type(record.document_type),
        "issued_date": record.issued_date,
        "venue": record.venue,
        "volume": record.volume,
        "issue": record.issue,
        "pages": record.pages,
        "article_number": record.article_number,
        "publisher": record.publisher,
        "doi": record.doi,
        "url": record.url or record.source_url,
    }
    merged_values = values.copy()
    merged_provenance = provenance.copy()
    conflicts: dict[str, tuple[str, ...]] = {}

    for field_name, incoming in resolver_values.items():
        current = merged_values[field_name]

        if _is_missing(current):
            merged_values[field_name] = incoming

            if not _is_missing(incoming):
                merged_provenance[field_name] = _DOI_PROVENANCE

            continue

        if _is_missing(incoming):
            continue

        if _values_match(field_name, current, incoming):
            # DOI 返回的结构化作者与更完整日期能提高各 CSL 样式的准确度，属于补足精度。
            if _prefer_resolver_value(field_name, current, incoming):
                merged_values[field_name] = incoming
                merged_provenance[field_name] = _DOI_PROVENANCE

            continue

        conflicts[field_name] = (_display_value(current), _display_value(incoming))

    return merged_values, merged_provenance, conflicts


def _is_missing(value: object) -> bool:
    """将 None、空字符串和空作者列表统一判定为未取得字段。"""
    return value is None or value == () or (isinstance(value, str) and not value.strip())


def _values_match(field_name: str, current: object, incoming: object) -> bool:
    """按字段语义比较，忽略大小写、姓名格式和仅有日期精度造成的伪冲突。"""
    if field_name == "authors":
        return _author_keys(current) == _author_keys(incoming)

    if field_name == "doi":
        return normalize_doi(_as_text(current)) == normalize_doi(_as_text(incoming))

    if field_name == "document_type":
        return normalize_document_type(_as_text(current)) == normalize_document_type(
            _as_text(incoming)
        )

    if field_name == "title":
        current_title = _as_text(current)
        incoming_title = _as_text(incoming)
        return (
            current_title is not None
            and incoming_title is not None
            and normalize_title_key(current_title) == normalize_title_key(incoming_title)
        )

    if field_name == "issued_date":
        return _dates_match(current, incoming)

    return normalize_optional_text(_as_text(current)) == normalize_optional_text(_as_text(incoming))


def _author_keys(value: object) -> tuple[str, ...]:
    """以作者顺序和规整姓名比较，避免不同展示格式被误判为冲突。"""
    if not isinstance(value, tuple):
        return ()

    return tuple(
        normalize_author_key(author.display_name()) or ""
        for author in value
        if isinstance(author, CitationAuthor)
    )


def _dates_match(current: object, incoming: object) -> bool:
    """年份相同且候选缺少月日时，允许 DOI 记录补足日期精度。"""
    if not isinstance(current, CitationDate) or not isinstance(incoming, CitationDate):
        return False

    if current.year != incoming.year:
        return False

    if current.month is not None and current.month != incoming.month:
        return False

    return current.day is None or current.day == incoming.day


def _prefer_resolver_value(field_name: str, current: object, incoming: object) -> bool:
    """只在语义一致但 DOI 更精确时采用它，绝不覆盖存在内容差异的候选字段。"""
    if field_name == "authors":
        return _authors_are_structured(incoming) and not _authors_are_structured(current)

    if field_name == "issued_date":
        return (
            isinstance(current, CitationDate)
            and isinstance(incoming, CitationDate)
            and (
                current.month is None
                and incoming.month is not None
                or current.day is None
                and incoming.day is not None
            )
        )

    return False


def _authors_are_structured(value: object) -> bool:
    """只有全部作者都带有姓氏时，才把该作者集当作可安全格式化的结构化姓名。"""
    return (
        isinstance(value, tuple)
        and bool(value)
        and all(
            isinstance(author, CitationAuthor) and author.family is not None for author in value
        )
    )


def _as_text(value: object) -> str | None:
    """为规整比较提取字符串；其他类型不会被隐式转换为展示文本。"""
    return value if isinstance(value, str) else None


def _display_value(value: object) -> str:
    """将冲突值压缩为安全的前端展示文本，不输出原始上游 JSON。"""
    if isinstance(value, tuple) and all(isinstance(item, CitationAuthor) for item in value):
        return "; ".join(item.display_name() for item in value)

    if isinstance(value, CitationDate):
        return "-".join(str(part) for part in value.to_csl_date_parts())

    return str(value)


def _build_metadata(
    *,
    values: dict[str, object],
    provenance: dict[str, str],
    conflicts: dict[str, tuple[str, ...]] | None = None,
    resolution_error: object | None = None,
) -> CitationMetadata:
    """根据完整度、冲突和网络结果生成最终题录状态。"""
    active_conflicts = conflicts or {}
    missing_fields = _missing_fields(values)

    if resolution_error is not None:
        status = CitationMetadataStatus.UNRESOLVED
    elif active_conflicts:
        status = CitationMetadataStatus.CONFLICT
    elif missing_fields:
        status = CitationMetadataStatus.PARTIAL
    else:
        status = CitationMetadataStatus.READY

    return CitationMetadata.model_validate(
        {
            "status": status,
            "authors": values["authors"],
            "title": values["title"],
            "document_type": values["document_type"],
            "issued_date": values["issued_date"],
            "venue": values["venue"],
            "volume": values["volume"],
            "issue": values["issue"],
            "pages": values["pages"],
            "article_number": values["article_number"],
            "publisher": values["publisher"],
            "doi": values["doi"],
            "url": values["url"],
            "missing_fields": missing_fields,
            "conflicts": active_conflicts,
            "field_provenance": provenance,
            "resolution_error": resolution_error,
        }
    )


def _missing_fields(values: dict[str, object]) -> tuple[str, ...]:
    """根据论文常用题录最小集合计算缺失字段，不把可选期号强制为错误。"""
    missing: list[str] = []

    for field_name in ("authors", "title", "document_type", "issued_date", "doi", "url"):
        if _is_missing(values[field_name]):
            missing.append(field_name)

    if values["document_type"] == "journal_article":
        for field_name in ("venue", "volume"):
            if _is_missing(values[field_name]):
                missing.append(field_name)

        if _is_missing(values["pages"]) and _is_missing(values["article_number"]):
            missing.append("pages_or_article_number")

    return tuple(missing)
