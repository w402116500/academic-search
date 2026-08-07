# academic-search 开发环境

状态：已配置。此环境用于本地开发和面试演示；已包含认证、工作区、研究计划、多源检索、候选审核、全文准入、集合构建与研究会话 API，以及意图分析、多源检索、全文获取、RAG 入库和研究 Worker。

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

另开四个终端分别启动工作流、相关性分析、RAG 文献入库和研究问答 Worker：

```powershell
uv run --directory backend arq app.workers.workflow.WorkerSettings
uv run --directory backend arq app.workers.relevance.WorkerSettings
uv run --directory backend arq app.workers.ingestion.WorkerSettings
uv run --directory backend arq app.workers.research.WorkerSettings
```

四类 Worker 都会从 `REDIS_URL` 连接 arq，但不共享任务队列：工作流 Worker 消费 `arq:queue:workflow` 中的意图分析、检索和候选全文任务；相关性 Worker 消费 `arq:queue:relevance` 中的候选相关性批量分析任务；入库 Worker 消费 `arq:queue:ingestion` 中的 PDF 解析、嵌入和 Milvus 写入任务；研究 Worker 消费 `arq:queue:research` 中的 RAG 问答任务。工作流 Worker 在用户调用 `POST /api/v1/collections/research` 后访问聊天模型，返回经过 Pydantic 校验的 2-3 个研究方向和方向对应检索表达式；检索运行与候选全文获取也由同一个 Worker 入口消费。它只在用户确认计划后调用 OpenAlex、Crossref、arXiv 和 Semantic Scholar，并将候选放入 Redis 短期会话；基础初筛后先发布候选快照并投递相关性 Worker。相关性 Worker 首轮以完整已初筛候选集合执行流式相关性判断，并只对通过该判断的候选执行流式独立主张核验；服务端逐条验证结果、原子合并有效同伴，再将未解决候选的 ID 保存在快照中安排一次批量重试。重试队列消息仍只携带搜索运行 ID 和尝试次数，成员从 Redis 快照派生。候选数量不会以固定阈值阻断首轮完整集合判断，也不会被隐式拆成串行批次；仅实际输入或预留输出限制可触发有界批次。候选全文仅可使用该会话中服务端已发现的直接 PDF URL。入库 Worker 只在用户确认构建集合后接收 `queued` 文献，随后访问 MinIO、PostgreSQL、OpenAI 兼容 embedding 服务和 Milvus。研究 Worker 使用当前集合中已完成入库的文献证据执行 RAG 问答，并把阶段进度与终态事件写回 PostgreSQL 和 Redis SSE。

意图分析、候选相关性评估和后续研究对话使用 `WORKFLOW_CHAT_PROVIDER` 选择聊天后端，当前默认值为 `deepseek`，对应 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_CHAT_MODEL`。如需切换到其他 OpenAI 兼容聊天服务，将其改为 `openai_compatible` 并配置 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_CHAT_MODEL`。`WORKFLOW_RELEVANCE_STREAM_IDLE_TIMEOUT_SECONDS=120` 只在当前相关性模型流连续 120 秒没有任何模型活动时失败；它不是整次调用总时长，也不限制候选数量或摘要长度。`WORKFLOW_RELEVANCE_OUTPUT_TOKENS_PER_CANDIDATE` 与 `WORKFLOW_RELEVANCE_VERIFICATION_OUTPUT_TOKENS_PER_CANDIDATE` 分别让相关性判断和独立核验的输出预算随当前批次的有摘要候选数增加。完整摘要和候选数量都不是截断或失败条件；`OPENAI_*` 也继续用于 RAG embedding 配置。模型输出不符合计划结构时，工作区会进入 `failed`，用户可修改原始要求并调用重新生成接口；候选相关性的技术异常只由专用 Worker 自动重试当前未解决子集一次，不重新请求文献来源，也不公开模型流正文、重试或取消控制。

确认研究计划后，前端调用 `POST /api/v1/collections/{collection_id}/search-runs` 显式启动多源检索。检索任务由 `app.workers.workflow.WorkerSettings` 消费，按已确认查询并发调用已启用来源，并通过下列接口恢复状态：

- `GET /api/v1/collections/{collection_id}/search-runs/current`：刷新页面时读取最近一次运行。
- `GET /api/v1/collections/{collection_id}/search-runs/{run_id}`：读取 PostgreSQL 中的运行状态和来源摘要。
- `GET /api/v1/collections/{collection_id}/search-runs/{run_id}/candidates`：读取 Redis TTL 内的统一候选。
- `GET /api/v1/collections/{collection_id}/search-runs/{run_id}/events`：通过 SSE 接收阶段、来源状态、计数和失败原因，并支持 `Last-Event-ID` 断线恢复。
- `POST /api/v1/collections/{collection_id}/search-runs/{run_id}/retry`：为失败、部分失败或过期运行创建新的尝试。

候选审核不把“正在查看”“本次准备清单”“待确认集合”和“可研究集合”混为同一状态。候选分页、跨页准备选择及全文短期状态继续属于 Redis 搜索会话；只有准备完成并满足严格准入的文献才写入 PostgreSQL 待确认集合。前端使用下列接口建立该流程：

- `GET /api/v1/collections/{collection_id}/search-runs/{run_id}/candidates?limit=&cursor=&query=&filter=`：服务端分页读取候选审核行。
- `PATCH` / `DELETE .../candidate-selection`：增加、移除或清空本次 Redis 准备清单。
- `POST .../candidate-selection/prepare`：按清单逐篇投递题录补齐与全文核验。
- `POST .../candidate-selection/admission`：仅将已满足题录和全文条件的候选加入 PostgreSQL 待确认集合。

全文下载默认直连，并且不会继承全局 `HTTP_PROXY` 或 `HTTPS_PROXY`。若某次开放获取验收需要显式使用本地代理，可只为该进程设置 `FULLTEXT_NETWORK_MODE=proxy` 和 `LITERATURE_PROXY_URL=http://127.0.0.1:7897`；这不会改变 MinIO、PostgreSQL、Redis、Milvus 或其他 Provider 的网络路由。

候选只在 Redis 中短期保存，未通过 DOI、全文和权限准入前不会写入 PostgreSQL 的 `papers`。真实多源运行验收默认关闭；需要显式设置 `RUN_LIVE_SEARCH_RUN_TESTS=1` 后运行：

```powershell
Set-Location backend
$env:RUN_LIVE_SEARCH_RUN_TESTS = "1"
uv run pytest tests/integration/test_live_search_run.py -m live -s
```

该测试会创建随机临时用户、工作区、研究计划和检索运行，结束时删除数据库与 Redis 数据；它会消耗外部文献来源配额，不应放入常规 CI。

全文准入与集合构建采用两个明确动作，而不是在下载完成后自动向量化：

```text
Redis 搜索候选
  -> 候选全文任务（queued / downloading / validating / available）
  -> POST .../fulltext/admission（写入 PostgreSQL，IngestionRun = pending）
  -> POST /api/v1/collections/{collection_id}/build（转为 queued 并投递 arq）
  -> ingestion Worker（parse / chunk / embed / index）
  -> completed + is_current=true（可用于 RAG）
```

可通过以下本地集成测试验证两段持久化边界；测试会创建并清理随机 PostgreSQL、Redis 或 MinIO 临时数据：

```powershell
Set-Location backend
$env:RUN_LIVE_COLLECTION_ADMISSION_TESTS = "1"
uv run pytest tests/integration/test_live_collection_admission.py -m live -s

$env:RUN_LIVE_COLLECTION_BUILD_TESTS = "1"
uv run pytest tests/integration/test_live_collection_build.py -m live -s
```

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
pnpm api:generate
pnpm api:check
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:e2e

Set-Location ../backend
uv run ruff check .
uv run ruff format --check .
uv run pyright

# 显式运行一次真实 DeepSeek 候选相关性验收；不会写入数据库、Redis 或对象存储。
$env:RUN_LIVE_CANDIDATE_RELEVANCE_TESTS = "1"
uv run pytest tests/integration/test_live_candidate_relevance.py -m live -s

# 真实 arXiv PDF 的候选审核 -> 全文 -> MinIO -> 批量准入验收。
$env:RUN_LIVE_CANDIDATE_REVIEW_E2E_TESTS = "1"
$env:FULLTEXT_NETWORK_MODE = "proxy"
$env:LITERATURE_PROXY_URL = "http://127.0.0.1:7897"
$env:FULLTEXT_DOWNLOAD_TIMEOUT_SECONDS = "45"
uv run pytest tests/integration/test_live_candidate_review_e2e.py -m live -s
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
