"""PDF 按页文本解析器。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pypdf
from app.modules.ingestion.contracts import (
    IngestionError,
    IngestionErrorCode,
    ParsedDocument,
    ParsedPage,
)
from pypdf.errors import PdfReadError


class PdfTextParser:
    """使用 pypdf 提取文字型 PDF；扫描件不会被伪装成空文本成功结果。"""

    parser_name = "pypdf"
    parser_version = pypdf.__version__

    async def parse(self, file_path: Path) -> ParsedDocument:
        """在线程池中读取 PDF，避免阻塞 arq 的事件循环。"""
        try:
            return await asyncio.to_thread(self._parse_sync, file_path)
        except IngestionError:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise IngestionError(
                IngestionErrorCode.PDF_PARSE_FAILED,
                "PDF 无法解析，可能已损坏或使用了不受支持的加密方式。",
            ) from exc

    def _parse_sync(self, file_path: Path) -> ParsedDocument:
        """同步解析实现；调用方已经在后台线程执行此方法。"""
        reader = pypdf.PdfReader(str(file_path), strict=False)

        if reader.is_encrypted:
            # 只尝试空密码；系统不保存或猜测用户的 PDF 密码。
            if reader.decrypt("") == 0:
                raise IngestionError(
                    IngestionErrorCode.PDF_PARSE_FAILED,
                    "PDF 受密码保护，无法在未提供密码的情况下解析。",
                )

        pages: list[ParsedPage] = []
        empty_page_numbers: list[int] = []

        for page_number, page in enumerate(reader.pages, start=1):
            extracted = page.extract_text() or ""
            text = _normalize_page_text(extracted)

            if text:
                pages.append(ParsedPage(page_number=page_number, text=text))
            else:
                empty_page_numbers.append(page_number)

        if not pages:
            raise IngestionError(
                IngestionErrorCode.PDF_NO_EXTRACTABLE_TEXT,
                "PDF 没有可提取的文字内容，当前版本不会把扫描件伪装成可检索文献。",
            )

        return ParsedDocument(
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            total_pages=len(reader.pages),
            pages=tuple(pages),
            empty_page_numbers=tuple(empty_page_numbers),
        )


def _normalize_page_text(value: str) -> str:
    """清理 PDF 文本层常见的控制符，但保留段落换行作为切块边界。"""
    lines = [
        re.sub(r"[ \t]+", " ", line).strip() for line in value.replace("\x00", "").splitlines()
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
