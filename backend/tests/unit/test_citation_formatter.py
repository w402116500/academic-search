"""CSL 与 BibTeX 引用输出的离线测试。"""

from __future__ import annotations

import pytest
from app.modules.literature.citation_formatter import (
    CitationFormat,
    CitationFormattingError,
    format_citation,
)
from app.modules.literature.contracts import (
    CitationAuthor,
    CitationDate,
    CitationMetadata,
    CitationMetadataStatus,
)


def _ready_metadata() -> CitationMetadata:
    """提供可用于所有引用样式的完整期刊题录。"""
    return CitationMetadata(
        status=CitationMetadataStatus.READY,
        authors=(CitationAuthor(given="Ada", family="Lovelace"),),
        title="Evidence for AI-supported academic writing",
        document_type="journal_article",
        issued_date=CitationDate(year=2024, month=5, day=1),
        venue="Journal of Research Methods",
        volume="12",
        issue="3",
        pages="101-115",
        publisher="Academic Press",
        doi="10.1000/crossref.example",
        url="https://doi.org/10.1000/crossref.example",
        field_provenance={"title": "doi_content_negotiation"},
    )


@pytest.mark.parametrize(
    "citation_format",
    (
        CitationFormat.GB_T_7714_2015_NUMERIC,
        CitationFormat.APA_7,
        CitationFormat.MLA_9,
        CitationFormat.CHICAGO_AUTHOR_DATE,
    ),
)
def test_csl_formats_are_rendered_from_the_same_verified_metadata(
    citation_format: CitationFormat,
) -> None:
    """各样式必须经 CSL 引擎生成，并保留标题和 DOI 这两个可核验标识。"""
    rendered = format_citation(_ready_metadata(), citation_format)

    assert rendered
    assert "evidence for ai-supported academic writing" in rendered.casefold()
    assert "10.1000/crossref.example" in rendered


def test_bibtex_is_serialized_from_the_same_verified_metadata() -> None:
    """BibTeX 由 pybtex 序列化，不能与 CSL 格式使用不同的字段来源。"""
    rendered = format_citation(_ready_metadata(), CitationFormat.BIBTEX)

    assert rendered.startswith("@article{")
    assert 'title = "Evidence for AI-supported academic writing"' in rendered
    assert 'doi = "10.1000/crossref.example"' in rendered


def test_incomplete_metadata_cannot_be_rendered_as_a_seemingly_complete_citation() -> None:
    """缺字段题录必须显式失败，避免用户复制到看似规范但实际不完整的引用。"""
    metadata = _ready_metadata().model_copy(
        update={
            "status": CitationMetadataStatus.PARTIAL,
            "missing_fields": ("volume",),
        }
    )

    with pytest.raises(CitationFormattingError, match="缺少字段：volume"):
        format_citation(metadata, CitationFormat.GB_T_7714_2015_NUMERIC)
