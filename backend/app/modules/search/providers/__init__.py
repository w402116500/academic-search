"""外部学术文献来源的适配器与注册表。"""

from app.modules.search.providers.arxiv import ArxivProvider
from app.modules.search.providers.crossref import CrossrefProvider
from app.modules.search.providers.openalex import OpenAlexProvider
from app.modules.search.providers.registry import ProviderRegistry, build_provider_registry
from app.modules.search.providers.semantic_scholar import SemanticScholarProvider

__all__ = [
    "ArxivProvider",
    "CrossrefProvider",
    "OpenAlexProvider",
    "ProviderRegistry",
    "SemanticScholarProvider",
    "build_provider_registry",
]
