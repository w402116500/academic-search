"""Academic Search 后端的 FastAPI 应用入口。

当前模块只负责 API 进程启动所需的公共能力。具体业务路由会在后续按领域
拆分到 app/ 目录，并通过 Router 注册到此应用。
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers.router import router as api_router
from app.core.env import load_env

# 在读取任何环境变量前加载项目根目录的 .env，命令行环境变量仍具有更高优先级。
load_env()


def get_cors_origins() -> list[str]:
    """读取允许访问 API 的前端来源，支持用逗号分隔多个地址。"""
    raw_origins = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


app = FastAPI(
    title="Academic Search API",
    version="0.1.0",
    description="面向学术文献检索与 RAG 研究工作区的后端服务。",
)

# 前端在本地开发时运行于独立端口，需要显式声明可跨域访问此 API。
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务接口统一通过 /api/v1 暴露，避免后续调整响应格式时破坏已有前端调用。
app.include_router(api_router)


@app.get(
    "/healthz",
    tags=["系统"],
    summary="检查 API 服务是否存活",
    description="仅确认 FastAPI 进程正在运行，不检查数据库或其他外部依赖。",
    response_description="API 进程存活状态。",
)
async def health_check() -> dict[str, str]:
    """返回 API 进程存活状态。"""
    return {"status": "ok"}


@app.get(
    "/",
    tags=["系统"],
    summary="获取 API 服务信息",
    description="提供服务名称和 OpenAPI 文档地址，方便确认 API 已启动。",
    response_description="服务基础信息。",
)
async def api_info() -> dict[str, str]:
    """提供简洁的服务信息，方便浏览器和部署探针确认 API 已启动。"""
    return {
        "name": "Academic Search API",
        "docs_url": "/docs",
    }


if __name__ == "__main__":
    # 方便直接执行 `python -m app.main`；日常热更新建议使用 uvicorn 的 --reload 参数。
    uvicorn.run(
        "app.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
    )
