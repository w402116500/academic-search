"""基于页码和段落边界的 L1/L2/L3 父子分块。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import tiktoken

from app.modules.rag.ingestion.contracts import DocumentChunkDraft, ParsedDocument, ParsedPage


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """首版可复现的三级切块参数，使用字符上限控制单次模型输入。"""

    max_l1_characters: int = 12_000
    max_l2_characters: int = 4_000
    max_l3_characters: int = 1_200
    l3_overlap_characters: int = 160
    tokenizer_encoding: str = "cl100k_base"

    def __post_init__(self) -> None:
        """在任务开始前拒绝会破坏父子层级的配置。"""
        if not (0 < self.max_l3_characters <= self.max_l2_characters <= self.max_l1_characters):
            raise ValueError("三级分块大小必须满足 L3 <= L2 <= L1 且都大于零")
        if not 0 <= self.l3_overlap_characters <= self.max_l3_characters - 3:
            raise ValueError("L3 重叠字符数必须为非负数，并为新增段落及分隔符保留空间")
        tiktoken.get_encoding(self.tokenizer_encoding)

    def as_dict(self) -> dict[str, int | str]:
        """返回可保存到 ``ingestion_runs.chunking_config`` 的配置快照。"""
        return {
            "max_l1_characters": self.max_l1_characters,
            "max_l2_characters": self.max_l2_characters,
            "max_l3_characters": self.max_l3_characters,
            "l3_overlap_characters": self.l3_overlap_characters,
            "tokenizer_encoding": self.tokenizer_encoding,
        }


@dataclass(frozen=True, slots=True)
class _TextSpan:
    """带原始页码、段落编号和段内字符位置的内部文本单元。"""

    page_number: int
    paragraph_number: int
    character_start: int
    character_end: int
    content: str
    is_overlap: bool = False


class HierarchicalChunker:
    """先建立段落单元，再逐层聚合为 L1、L2 和 L3。"""

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        """保存不可变配置，确保同一运行中的所有块使用相同规则。"""
        self.config = config or ChunkingConfig()
        self._encoding = tiktoken.get_encoding(self.config.tokenizer_encoding)

    def build(self, document: ParsedDocument) -> tuple[DocumentChunkDraft, ...]:
        """为文档生成有稳定顺序和完整父子血缘的三级块。"""
        spans = tuple(_page_spans(document.pages))

        if not spans:
            raise ValueError("解析结果没有可切块的文本")

        drafts: list[DocumentChunkDraft] = []
        ordinal = 0

        for l1_spans in _partition_spans(spans, self.config.max_l1_characters):
            l1 = self._draft(
                spans=l1_spans,
                level=1,
                parent_chunk_id=None,
                root_chunk_id=None,
                ordinal=ordinal,
            )
            ordinal += 1
            drafts.append(l1)

            for l2_spans in _partition_spans(l1_spans, self.config.max_l2_characters):
                l2 = self._draft(
                    spans=l2_spans,
                    level=2,
                    parent_chunk_id=l1.id,
                    root_chunk_id=l1.id,
                    ordinal=ordinal,
                )
                ordinal += 1
                drafts.append(l2)

                for l3_spans in _partition_spans(
                    l2_spans,
                    self.config.max_l3_characters,
                    overlap_characters=self.config.l3_overlap_characters,
                ):
                    l3 = self._draft(
                        spans=l3_spans,
                        level=3,
                        parent_chunk_id=l2.id,
                        root_chunk_id=l1.id,
                        ordinal=ordinal,
                    )
                    ordinal += 1
                    drafts.append(l3)

        return tuple(drafts)

    def _draft(
        self,
        *,
        spans: Sequence[_TextSpan],
        level: int,
        parent_chunk_id: UUID | None,
        root_chunk_id: UUID | None,
        ordinal: int,
    ) -> DocumentChunkDraft:
        """把一组文本单元转为持久化草稿并记录可回查定位信息。"""
        content = "\n\n".join(span.content for span in spans).strip()
        page_start = min(span.page_number for span in spans)
        page_end = max(span.page_number for span in spans)
        chunk_id = uuid4()
        return DocumentChunkDraft(
            id=chunk_id,
            parent_chunk_id=parent_chunk_id,
            root_chunk_id=root_chunk_id or chunk_id,
            level=level,
            ordinal=ordinal,
            content=content,
            token_count=len(self._encoding.encode(content)),
            page_start=page_start,
            page_end=page_end,
            section_path=None,
            locator={
                "page_start": page_start,
                "page_end": page_end,
                "paragraph_start": spans[0].paragraph_number,
                "paragraph_end": spans[-1].paragraph_number,
                "character_start": spans[0].character_start,
                "character_end": spans[-1].character_end,
                "overlap_characters": len(spans[0].content) if spans[0].is_overlap else 0,
            },
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )


def _page_spans(pages: Iterable[ParsedPage]) -> Iterable[_TextSpan]:
    """按空行切分页面段落，并保留页码作为引用定位的第一层锚点。"""
    for page in pages:
        paragraphs = re.split(r"\n\s*\n", page.text)
        for paragraph_number, paragraph in enumerate(paragraphs, start=1):
            content = paragraph.strip()
            if content:
                yield _TextSpan(
                    page_number=page.page_number,
                    paragraph_number=paragraph_number,
                    character_start=0,
                    character_end=len(content),
                    content=content,
                )


def _partition_spans(
    spans: Sequence[_TextSpan],
    max_characters: int,
    *,
    overlap_characters: int = 0,
) -> list[list[_TextSpan]]:
    """将段落聚合到上限内，并只在 L3 层添加明确的前文重叠。"""
    expanded: list[_TextSpan] = []
    # 有重叠时为下一块预留前文空间，避免“重叠 + 新段落”超过 L3 上限。
    span_limit = max_characters - overlap_characters - 2 if overlap_characters else max_characters
    for span in spans:
        expanded.extend(_split_span(span, span_limit))

    groups: list[list[_TextSpan]] = []
    current: list[_TextSpan] = []
    current_length = 0

    for span in expanded:
        separator_length = 2 if current else 0
        if current and current_length + separator_length + len(span.content) > max_characters:
            groups.append(current)
            overlap = _tail_span(current, overlap_characters)
            current = [overlap] if overlap is not None else []
            current_length = len(overlap.content) if overlap is not None else 0
            separator_length = 2 if current else 0

        current.append(span)
        current_length += separator_length + len(span.content)

    if current:
        groups.append(current)

    return groups


def _split_span(span: _TextSpan, max_characters: int) -> list[_TextSpan]:
    """处理单个超长段落，优先在空白处断开以避免切断单词。"""
    if len(span.content) <= max_characters:
        return [span]

    result: list[_TextSpan] = []
    start = 0
    while start < len(span.content):
        end = min(start + max_characters, len(span.content))
        if end < len(span.content):
            whitespace = span.content.rfind(" ", start + max_characters // 2, end)
            if whitespace > start:
                end = whitespace
        piece = span.content[start:end].strip()
        if piece:
            result.append(
                replace(
                    span,
                    character_start=span.character_start + start,
                    character_end=span.character_start + end,
                    content=piece,
                )
            )
        start = end
        while start < len(span.content) and span.content[start].isspace():
            start += 1
    return result


def _tail_span(spans: Sequence[_TextSpan], overlap_characters: int) -> _TextSpan | None:
    """提取上一块尾部作为下一块的重叠前文，同时保留原页码和段落定位。"""
    if overlap_characters <= 0 or not spans:
        return None
    tail = spans[-1]
    content = tail.content[-overlap_characters:]
    if not content:
        return None
    return replace(
        tail,
        character_start=tail.character_end - len(content),
        content=content,
        is_overlap=True,
    )
