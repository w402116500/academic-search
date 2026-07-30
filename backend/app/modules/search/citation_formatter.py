"""基于格式中立题录生成标准引用文本。"""

from __future__ import annotations

import re
from enum import StrEnum

from app.modules.search.contracts import CitationAuthor, CitationMetadata, CitationMetadataStatus
from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle
from citeproc import formatter as citeproc_formatter
from citeproc.source.json import CiteProcJSON
from pybtex.database import BibliographyData, Entry, Person


class CitationFormat(StrEnum):
    """当前可从同一份已核验题录导出的引用格式。"""

    GB_T_7714_2015_NUMERIC = "gb_t_7714_2015_numeric"
    APA_7 = "apa_7"
    MLA_9 = "mla_9"
    CHICAGO_AUTHOR_DATE = "chicago_author_date"
    BIBTEX = "bibtex"


class CitationFormattingError(ValueError):
    """题录尚不可安全格式化时抛出的明确业务错误。"""


_CSL_STYLE_NAMES = {
    CitationFormat.GB_T_7714_2015_NUMERIC: "china-national-standard-gb-t-7714-2015-numeric",
    # citeproc-py-styles 中的 ``apa``、MLA 与 Chicago 样式均为各自当前主版本。
    CitationFormat.APA_7: "apa",
    CitationFormat.MLA_9: "modern-language-association",
    CitationFormat.CHICAGO_AUTHOR_DATE: "chicago-author-date",
}
_CSL_DOCUMENT_TYPES = {
    "journal_article": "article-journal",
    "conference_paper": "paper-conference",
    "book": "book",
    "book_chapter": "chapter",
    "dissertation": "thesis",
    "preprint": "article",
    "posted_content": "article",
    "dataset": "dataset",
    "editorial": "article",
    "correction": "article",
    "peer_review": "article",
    "reference_entry": "entry-encyclopedia",
    "other": "article",
}
_BIBTEX_ENTRY_TYPES = {
    "journal_article": "article",
    "conference_paper": "inproceedings",
    "book": "book",
    "book_chapter": "incollection",
    "dissertation": "phdthesis",
}


def format_citation(metadata: CitationMetadata, citation_format: CitationFormat) -> str:
    """从同一份 READY 题录生成指定格式，禁止为不完整或冲突结果编造引用。"""
    _require_ready_metadata(metadata)

    if citation_format is CitationFormat.BIBTEX:
        return _format_bibtex(metadata)

    return _format_with_csl(metadata, citation_format)


def _require_ready_metadata(metadata: CitationMetadata) -> None:
    """将不可格式化原因合并成操作方能直接展示或记录的明确错误。"""
    if metadata.status is CitationMetadataStatus.READY:
        return

    details: list[str] = [f"题录状态为 {metadata.status.value}"]

    if metadata.missing_fields:
        details.append(f"缺少字段：{', '.join(metadata.missing_fields)}")

    if metadata.conflicts:
        details.append(f"存在冲突字段：{', '.join(metadata.conflicts)}")

    if metadata.resolution_error is not None:
        details.append(metadata.resolution_error.message)

    raise CitationFormattingError("；".join(details))


def _format_with_csl(metadata: CitationMetadata, citation_format: CitationFormat) -> str:
    """使用 CSL 样式引擎渲染人类可读的参考文献，而非自行拼接字符串。"""
    style_name = _CSL_STYLE_NAMES[citation_format]
    csl_record = _to_csl_json(metadata)
    source = CiteProcJSON([csl_record])
    style = CitationStylesStyle(style_name, validate=False)
    bibliography = CitationStylesBibliography(style, source, formatter=citeproc_formatter.plain)
    citation = Citation([CitationItem(csl_record["id"])])
    bibliography.register(citation)
    bibliography.sort()
    entries = bibliography.bibliography()

    if len(entries) != 1:
        raise CitationFormattingError("CSL 引用引擎未返回唯一的参考文献条目")

    return str(entries[0]).strip()


def _to_csl_json(metadata: CitationMetadata) -> dict[str, object]:
    """将内部题录显式映射到 CSL-JSON，确保所有显示样式使用完全相同的数据。"""
    assert metadata.document_type is not None
    assert metadata.issued_date is not None

    record: dict[str, object] = {
        "id": _citation_key(metadata),
        "type": _CSL_DOCUMENT_TYPES.get(metadata.document_type, "article"),
        "title": metadata.title,
        "author": [author.to_csl_json() for author in metadata.authors],
        "issued": {"date-parts": [metadata.issued_date.to_csl_date_parts()]},
        "DOI": metadata.doi,
        "URL": metadata.url,
    }
    optional_fields = {
        "container-title": metadata.venue,
        "volume": metadata.volume,
        "issue": metadata.issue,
        # citeproc-py 尚不识别 CSL 的 article-number，文章号作为页码传入可被样式正确展示。
        "page": metadata.pages or metadata.article_number,
        "publisher": metadata.publisher,
    }

    record.update({field_name: value for field_name, value in optional_fields.items() if value})
    return record


def _format_bibtex(metadata: CitationMetadata) -> str:
    """通过 pybtex 序列化 BibTeX，避免为不同字段组合维护手写模板。"""
    assert metadata.document_type is not None
    assert metadata.issued_date is not None
    key = _citation_key(metadata)
    fields = {
        "title": metadata.title,
        "year": str(metadata.issued_date.year),
        "doi": metadata.doi,
        "url": metadata.url,
        "volume": metadata.volume,
        "number": metadata.issue,
        "pages": metadata.pages or metadata.article_number,
        "publisher": metadata.publisher,
    }

    if metadata.document_type == "journal_article":
        fields["journal"] = metadata.venue
    elif metadata.document_type in {"conference_paper", "book_chapter"}:
        fields["booktitle"] = metadata.venue

    entry = Entry(
        _BIBTEX_ENTRY_TYPES.get(metadata.document_type, "misc"),
        fields={field_name: value for field_name, value in fields.items() if value},
        persons={"author": [_to_bibtex_person(author) for author in metadata.authors]},
    )
    bibliography = BibliographyData(entries={key: entry})
    return bibliography.to_string("bibtex").strip()


def _to_bibtex_person(author: CitationAuthor) -> Person:
    """保留 DOI 返回的姓与名；字面名称保持原样以避免多语言姓名被错误拆分。"""
    if author.literal is not None:
        return Person(author.literal)

    assert author.family is not None
    return Person(f"{author.family}, {author.given}" if author.given else author.family)


def _citation_key(metadata: CitationMetadata) -> str:
    """用 DOI 生成稳定且兼容 BibTeX 的键；此键只服务格式化，不写入数据库。"""
    identifier = metadata.doi or metadata.title
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", identifier).strip("_").lower()
    return f"citation_{normalized[:80] or 'record'}"
