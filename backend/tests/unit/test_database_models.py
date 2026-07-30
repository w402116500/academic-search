"""PostgreSQL 模型准入规则的无数据库回归测试。"""

from app.db.models import Base
from sqlalchemy import CheckConstraint
from sqlalchemy.orm import configure_mappers

EXPECTED_TABLES = {
    "users",
    "research_collections",
    "papers",
    "collection_papers",
    "documents",
    "ingestion_runs",
    "document_chunks",
    "conversations",
    "messages",
    "research_runs",
    "research_evidences",
}


def test_initial_schema_contains_the_documented_tables() -> None:
    """首版模型应与数据库讨论稿中的表清单一致。"""
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_relationships_and_database_column_names_are_configurable() -> None:
    """关系映射应完整，且消息扩展数据仍映射为 metadata 列。"""
    configure_mappers()

    messages = Base.metadata.tables["messages"]
    papers = Base.metadata.tables["papers"]

    assert "metadata" in messages.c
    assert "metadata_json" not in messages.c
    assert "authors" in papers.c
    assert "citation_style" not in papers.c


def test_papers_express_the_doi_and_citation_metadata_admission_rules() -> None:
    """长期论文必须有 DOI，并保存重建多格式引用所需的规范字段。"""
    papers = Base.metadata.tables["papers"]
    doi_index = next(index for index in papers.indexes if index.name == "uq_papers_doi")
    expected_columns = {
        "doi",
        "publication_month",
        "publication_day",
        "volume",
        "issue",
        "pages",
        "article_number",
        "publisher",
    }

    assert not papers.c.doi.nullable
    assert doi_index.unique
    assert doi_index.dialect_options["postgresql"].get("where") is None
    assert "bibliographic_fingerprint" not in papers.c
    assert expected_columns <= set(papers.c.keys())
    assert {constraint.name for constraint in papers.constraints} >= {
        "ck_papers_publication_month_range",
        "ck_papers_publication_day_range",
        "ck_papers_publication_day_requires_month",
        "ck_papers_paper_type",
    }


def test_document_and_ingestion_models_keep_rag_source_and_version_boundaries() -> None:
    """正文来源与当前入库版本必须在模型层受到数据库约束。"""
    documents = Base.metadata.tables["documents"]
    ingestion_runs = Base.metadata.tables["ingestion_runs"]
    current_index = next(
        index
        for index in ingestion_runs.indexes
        if index.name == "uq_ingestion_runs_current_document"
    )

    origin_kind_constraint = next(
        constraint
        for constraint in documents.constraints
        if constraint.name == "ck_documents_origin_kind"
    )
    current_version_constraint = next(
        constraint
        for constraint in ingestion_runs.constraints
        if constraint.name == "ck_ingestion_runs_current_requires_completed"
    )

    assert isinstance(origin_kind_constraint, CheckConstraint)
    assert "official_download" in str(origin_kind_constraint.sqltext)
    assert "is_current" in ingestion_runs.c
    assert current_index.unique
    assert str(current_index.dialect_options["postgresql"]["where"]) == "is_current"
    assert isinstance(current_version_constraint, CheckConstraint)
    assert "status = 'completed'" in str(current_version_constraint.sqltext)


def test_all_documented_tables_and_columns_have_chinese_comments() -> None:
    """模型备注既作为源码说明，也会通过 Alembic 写入 PostgreSQL。"""
    for table in Base.metadata.tables.values():
        assert table.comment
        assert all(column.comment for column in table.columns)
