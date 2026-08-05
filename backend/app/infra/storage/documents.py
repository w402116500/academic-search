"""全文获取阶段使用的私有 S3 兼容对象存储适配器。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, BinaryIO

import boto3
from boto3.exceptions import S3UploadFailedError
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.fulltext_settings import FulltextAcquisitionSettings
from app.modules.documents.storage import FulltextStorageError


class Boto3StagingObjectStorage:
    """通过 boto3 写入 MinIO、OSS、COS 等 S3 兼容私有 bucket。"""

    def __init__(
        self,
        settings: FulltextAcquisitionSettings,
        *,
        client: Any | None = None,
    ) -> None:
        """构造 S3 客户端；测试可注入假 client 而无需网络连接。"""
        self._settings = settings
        self._client = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            config=Config(
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"}
            ),
        )

    async def upload_pdf(
        self,
        *,
        object_key: str,
        file: BinaryIO,
        sha256: str,
    ) -> None:
        """在线程中执行 boto3 阻塞上传，避免占用 FastAPI 或 arq 的事件循环。"""
        try:
            await asyncio.to_thread(self._upload_pdf_sync, object_key, file, sha256)
        except (BotoCoreError, ClientError, OSError, S3UploadFailedError) as exc:
            raise FulltextStorageError("无法将已校验的全文写入私有对象存储") from exc

    def _upload_pdf_sync(self, object_key: str, file: BinaryIO, sha256: str) -> None:
        """将暂存文件从开头上传，并附加不可替代的完整性元数据。"""
        file.seek(0)
        self._client.upload_fileobj(
            file,
            self._settings.s3_bucket,
            object_key,
            ExtraArgs={
                "ContentType": "application/pdf",
                "Metadata": {"sha256": sha256},
            },
        )

    async def promote_staged_pdf(
        self,
        *,
        staging_object_key: str,
        document_object_key: str,
        sha256: str,
    ) -> None:
        """将通过校验的暂存对象复制为正式文献，并删除原暂存对象。"""
        try:
            await asyncio.to_thread(
                self._promote_staged_pdf_sync,
                staging_object_key,
                document_object_key,
                sha256,
            )
        except (BotoCoreError, ClientError, OSError, S3UploadFailedError) as exc:
            raise FulltextStorageError("无法将暂存全文转为正式文献对象") from exc

    async def delete_object(self, *, object_key: str) -> None:
        """在线程中删除指定对象；S3 的删除操作天然幂等。"""
        try:
            await asyncio.to_thread(
                self._client.delete_object, Bucket=self._settings.s3_bucket, Key=object_key
            )
        except (BotoCoreError, ClientError, OSError, S3UploadFailedError) as exc:
            raise FulltextStorageError("无法清理对象存储中的全文对象") from exc

    async def download_object_to_file(self, *, object_key: str, destination: Path) -> None:
        """在线程中下载正式 PDF，供后续解析器读取临时文件。"""
        try:
            await asyncio.to_thread(self._download_object_to_file_sync, object_key, destination)
        except (BotoCoreError, ClientError, OSError, S3UploadFailedError) as exc:
            raise FulltextStorageError("无法从私有对象存储读取全文文件") from exc

    def _promote_staged_pdf_sync(
        self,
        staging_object_key: str,
        document_object_key: str,
        sha256: str,
    ) -> None:
        """使用服务端复制保留 PDF 内容，不将已下载正文重新读回应用进程。"""
        self._client.copy_object(
            Bucket=self._settings.s3_bucket,
            Key=document_object_key,
            CopySource={"Bucket": self._settings.s3_bucket, "Key": staging_object_key},
            MetadataDirective="REPLACE",
            ContentType="application/pdf",
            Metadata={"sha256": sha256},
        )
        self._client.delete_object(Bucket=self._settings.s3_bucket, Key=staging_object_key)

    def _download_object_to_file_sync(self, object_key: str, destination: Path) -> None:
        """使用 boto3 流式下载对象，避免将完整 PDF 载入 Worker 内存。"""
        with destination.open("wb") as file:
            self._client.download_fileobj(self._settings.s3_bucket, object_key, file)
