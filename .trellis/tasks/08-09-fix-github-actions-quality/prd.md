# 修复 GitHub Actions Quality 工作流失败

## Goal

修复 GitHub Actions `Quality` 工作流的启动和测试选择问题，让常规推送到 `main`
后能够稳定执行离线质量门禁，而不是在工具初始化、Action 版本解析或真实依赖测试上提前失败。

## Requirements

- 前端 job 必须在使用 `actions/setup-node` 的 pnpm 缓存前提供可执行的 pnpm。
- 基础设施 job 必须引用 GitHub 能解析到的 Trivy Action tag。
- 后端普通 CI 必须默认跳过 `@pytest.mark.live` 的真实集成测试，避免依赖 MinIO、
  Milvus、外部 API 或本地 `.env` 才能通过。
- 不读取、打印或提交任何 `.env`、Cookie、LocalStorage、测试账户密码、API key 或其他凭据。
- 本任务只修复 CI 配置和必要的测试选择约束，不调整业务功能、不启动重复服务。

## Acceptance Criteria

- [x] `.github/workflows/quality.yml` 中前端 job 的 pnpm 初始化顺序能支持 pnpm 缓存和安装。
- [x] `.github/workflows/quality.yml` 中 Trivy Action 引用使用存在的版本 tag。
- [x] `.github/workflows/quality.yml` 中后端 pytest 命令默认排除 live 测试。
- [x] 本地完成与改动相称的验证：后端非 live 测试选择可执行、前端依赖脚本配置可解析、
      Docker Compose 配置仍可解析，且 `git diff --check` 通过。

## Notes

- 这是轻量任务，PRD-only 足够；用户已在方案说明后明确要求“修复吧”。
- 已确认失败证据：
  - frontend：`actions/setup-node@v4` 步骤找不到 `pnpm`。
  - infrastructure：`aquasecurity/trivy-action@0.28.0` 无法解析，实际 tag 带 `v`。
  - backend：静态检查通过，失败发生在 `pytest`；普通质量流水线不应默认运行 live 测试。
