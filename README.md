# academic-search

面向学术文献发现、研究集合管理与可追溯 RAG 问答的面试项目。

本仓库已包含 FastAPI 的认证、工作区、研究计划、多源检索、全文准入与集合构建 API，
以及意图分析、检索、全文获取和 RAG 入库 Worker。Vue 主流程页面已覆盖认证、研究计划、检索、文献审核与集合构建；RAG 研究对话 API 与页面仍待实现。

## 本地开发

前端和后端在宿主机运行，Docker Compose 只启动有状态服务。

```powershell
Copy-Item infra/compose/.env.example infra/compose/.env
docker compose --env-file infra/compose/.env -f infra/compose/compose.dev.yml up -d

Set-Location frontend
pnpm install --frozen-lockfile

Set-Location ../backend
uv sync --frozen --all-groups
```

本机 PostgreSQL 使用 `localhost:55432`，Redis 使用 `localhost:6379`，Milvus 使用 `localhost:19530`，MinIO API 与 Console 分别使用 `localhost:9000` 和 `localhost:9001`。

更多命令、环境变量、验证步骤和排错方式见 [开发环境说明](docs/08-development-environment.md)。
