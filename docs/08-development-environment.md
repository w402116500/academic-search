# academic-search 开发环境

状态：已配置。此环境用于本地开发和面试演示；已包含认证、工作区、研究计划 API，以及意图分析和 RAG 入库 Worker。多源检索与研究 Agent 尚未实现。

## 1. 运行模型

```text
浏览器 <- Vite（宿主机，未来）
             |
        FastAPI（宿主机）/ arq Worker（宿主机）
             |
Docker Compose：PostgreSQL、Redis、etcd、Milvus、MinIO
```

前端使用 pnpm，后端使用 uv。Docker Compose 不运行 Web、API 或 Worker，以便后续业务开发获得直接的热更新与断点调试体验。

## 2. 前置条件

- Node.js `20.19.6` 与 Corepack。
- pnpm `10.34.5`。
- uv `0.11.3`，它会使用项目指定的 CPython `3.12.13`。
- Docker Desktop 与 Docker Compose。

## 3. 首次启动

在仓库根目录执行：

```powershell
Copy-Item infra/compose/.env.example infra/compose/.env
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env

docker compose --env-file infra/compose/.env -f infra/compose/compose.dev.yml up -d

Set-Location frontend
pnpm install --frozen-lockfile

Set-Location ../backend
uv sync --frozen --all-groups
uv run pre-commit install
```

完成上述依赖安装后，可在仓库根目录启动最小 API：

```powershell
uv run --directory backend uvicorn app.main:app --reload
```

首次使用账号和研究工作区 API 前，还需要在项目根目录 `.env` 设置
`AUTH_JWT_SECRET_KEY`。它必须至少包含 32 个随机字符，可用下列命令生成：

```powershell
uv run --directory backend python -c "import secrets; print(secrets.token_urlsafe(48))"
```

访问 `http://127.0.0.1:8000/docs` 可查看 OpenAPI 文档；`GET /healthz` 仅用于确认 API 进程存活，不检查外部服务。

另开两个终端分别启动研究意图分析和 RAG 文献入库 Worker：

```powershell
uv run --directory backend arq app.workers.workflow.WorkerSettings
uv run --directory backend arq app.workers.ingestion.WorkerSettings
```

两个 Worker 都会从 `REDIS_URL` 连接 arq 队列。意图分析 Worker 在用户调用 `POST /api/v1/collections/research` 后访问 OpenAI 兼容 Chat 模型，返回经过 Pydantic 校验的 2-3 个研究方向和方向对应检索表达式；它不会自动开始文献检索。入库 Worker 在后续全文准入任务投递后，访问 MinIO、PostgreSQL、OpenAI 兼容 embedding 服务和 Milvus。

意图分析模型使用 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_CHAT_MODEL` 和可选的 `WORKFLOW_INTENT_TIMEOUT_SECONDS`。模型输出不符合计划结构时，工作区会进入 `failed`，用户可修改原始要求并调用重新生成接口；系统不会把自由文本直接作为检索词执行。

本地 Docker Redis 仅映射 IPv4 时，推荐使用 `REDIS_URL=redis://127.0.0.1:6379/0`。Worker 会兼容旧的 `localhost` 配置并自动转为该 IPv4 地址；远程 Redis 地址保持原样。

## 4. 文献源网络配置

后端从项目根目录 `.env` 读取文献来源配置。网络路由与数据访问通道是两个独立概念：

- `*_NETWORK_MODE=direct`：该来源直连，不读取进程的 `HTTP_PROXY` 或 `HTTPS_PROXY`。
- `*_NETWORK_MODE=proxy`：该来源显式使用 `LITERATURE_PROXY_URL`；缺少该地址时应用启动会报配置错误。
- `SEMANTIC_SCHOLAR_ACCESS_MODE=official`：请求官方 API，使用 `SEMANTIC_SCHOLAR_API_KEY` 和 `x-api-key` 请求头。
- `SEMANTIC_SCHOLAR_ACCESS_MODE=ominiai`：请求 S2API Ominiai 兼容网关，使用 `S2API_OMINIAI_API_KEY` 和 Bearer 请求头。

当前本地网络的推荐路由如下：

| 来源 | `NETWORK_MODE` | 说明 |
|---|---|---|
| OpenAlex | `proxy` | 当前网络下经本地代理更稳定 |
| Crossref | `direct` | 已验证可直连 |
| arXiv | `direct` | 避免代理出口触发 429 |
| Semantic Scholar | `direct` | Ominiai 兼容网关已验证可直连 |

来源失败不会自动切换直连、代理或另一访问通道。实时检索测试会输出各来源实际使用的网络路由，以及 Semantic Scholar 的访问通道，便于定位限流和网络问题。

## 5. 服务地址

| 服务 | 地址 | 用途 |
| API | `http://localhost:8000` | FastAPI 最小应用入口与 OpenAPI 文档 |
|---|---|---|
| PostgreSQL | `localhost:55432` | 用户、文献、任务与权限等业务真相 |
| Redis | `localhost:6379` | 缓存、队列、限流与事件 |
| Milvus | `localhost:19530` | 文献片段向量检索 |
| Milvus health | `http://localhost:9091/healthz` | 健康检查 |
| MinIO API | `http://localhost:9000` | 本地 S3 兼容对象存储 |
| MinIO Console | `http://localhost:9001` | 本地对象存储管理界面 |

PostgreSQL 使用 `55432`，避免占用本机已被 WSL 转发服务使用的 `5432`。

## 6. 存储边界

MinIO 仅限本地开发和演示，使用最后公开镜像 `minio/minio:RELEASE.2025-09-07T16-13-09Z`。它不属于生产环境的长期依赖。

- `academic-documents`：论文原文、解析文本、页级产物。
- `milvus-data`：Milvus 内部段、索引与元数据文件。

两者必须保持为独立 bucket。部署时应用的 S3 客户端直接改用 OSS 或 COS 的 endpoint、region、bucket 和凭证；现有对象需通过迁移工具复制，切换配置不会自动搬运数据。

应用统一使用下列环境变量：`S3_ENDPOINT_URL`、`S3_REGION`、`S3_BUCKET`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_FORCE_PATH_STYLE`。本地 `S3_FORCE_PATH_STYLE=true`；OSS/COS 按目标服务兼容性调整。

## 7. 验证与维护

```powershell
docker compose --env-file infra/compose/.env -f infra/compose/compose.dev.yml ps
docker compose --env-file infra/compose/.env -f infra/compose/compose.dev.yml logs -f milvus

Set-Location frontend
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:e2e

Set-Location ../backend
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

停止服务：

```powershell
docker compose --env-file infra/compose/.env -f infra/compose/compose.dev.yml down
```

重置所有本地数据（不可恢复）：

```powershell
docker compose --env-file infra/compose/.env -f infra/compose/compose.dev.yml down -v
```

若 `55432`、`6379`、`9000`、`9001`、`19530` 或 `9091` 被占用，在 `infra/compose/.env` 修改相应端口，并同步更新 `backend/.env` 中的连接地址。
