# Academic Search Agent Rules

本文件适用于仓库中的所有开发、调试、评审和文档任务。目标是交付可验证的根因修复，同时控制验证成本；测试用于证明行为，不用于制造“做了很多检查”的表象。

## 1. 项目基线

- 后端：Python 3.12、FastAPI、SQLAlchemy、Pydantic、arq，依赖与命令由 `uv` 管理。
- 前端：Vue 3、TypeScript、Pinia、TanStack Query、Vite，依赖与命令由 `pnpm` 管理。
- PostgreSQL 保存持久业务事实；Redis 保存短期搜索会话、队列、租约与事件；MinIO 保存文献对象；Milvus 保存可重建的向量索引。
- 工作流跨越 API、Worker、Redis 会话、数据库状态和前端恢复逻辑。修改状态或协议时，先确认事实所有者和恢复路径，不要只修当前页面或当前进程内的症状。

## 2. 沟通与范围

- 默认使用中文回复，除非用户明确要求其他语言。
- 像务实的高级协作者一样沟通：先给结论，再给足以验证结论的依据；不奉承，不写空泛套话。
- 在多步骤编码任务第一次调用工具前，用 1-2 句话说明理解、第一步和必要假设。
- 有低风险合理假设时，简短说明后继续；只有缺失信息会实质改变方案或带来明显风险时才提问。
- 只实现用户明确要求的内容。不要顺手增加功能、进行无关重构或提出宽泛的后续建议。
- 工作区可能包含用户尚未提交的修改。只触碰任务所需文件，不覆盖、不回退、不格式化无关改动。

## 3. 代码库理解

- 仓库根目录存在 `.codegraph/` 时，在 `rg`、目录遍历或逐文件阅读之前先使用 CodeGraph 理解符号、调用路径和动态分派；不存在时直接使用 `rg`。
- 修改前定位：行为入口、事实所有者、持久化边界、调用者、现有测试和失败路径。
- 优先阅读项目已有 facade、contract、reducer、service 和测试模式，不建立平行实现。
- 开始任务前扫描可用 skills；匹配时完整阅读对应 `SKILL.md` 并告知用户。前端页面设计规则见本文末尾。

## 4. 根因优先

- 让错误、异常、日志和失败测试清楚暴露。不得增加静默 fallback、伪成功路径、吞异常的宽泛 `try/except`，也不得用默认值掩盖坏数据。
- 从不变量出发修复根因。若问题来自重复状态、过度 gate 或错误所有权，删除冗余逻辑并把事实归还真正所有者，不再加一层旁路。
- 业务逻辑不得硬依赖具体基础设施实现；通过参数、协议或所有者 facade 注入依赖。
- 优先短函数、浅层控制流、早返回、具名常量和不可变输入。注释解释意图与取舍，不复述代码。
- 遵循 SOLID、DRY、关注点分离和 YAGNI；但不要为了抽象而抽象。

以下情况按结构性修改处理，而不是局部热修：

- 重复业务规则或多个事实来源；
- 共享校验、权限、路由、缓存或状态机；
- API contract、事件结构、schema 或 migration；
- API、Worker、前端之间的跨模块行为；
- 持久完成状态、重试、租约、版本或 fencing；
- 同类 bug 重复出现，或测试依赖隐藏 fallback 才能通过。

结构性修改先写清不变量、所有者、受影响文件、迁移/兼容要求和验证接缝，再实施。移除已被替代的旧逻辑，避免保留第二套真相。

## 5. Review Smell Checklist

在方案阶段尽早识别并解决以下异味：

| 异味 | 典型表现 | 修复方向 |
| --- | --- | --- |
| 僵化 Rigidity | 一次状态变化需要修改多个无关模块 | 把事实或命令移到真正所有者 |
| 冗余 Redundancy | 多个消费者各自解析同一事件或 wire shape | 在 canonical type、contract 或 reducer 集中 |
| 循环 Circularity | 两个模块互相导入内部路径 | 使用所有者 facade、稳定 ID 或已提交事件 |
| 脆弱 Fragility | 仅靠进程内存判断持久任务已完成 | 持久化状态、版本和 fencing 证据 |
| 晦涩 Obscurity | 通用 service/repository 隐藏产品动作 | 显式命名业务命令与所有者 |
| 数据团 Data clump | ID、version、plan hash 总是分散同行 | 定义有类型的 command/contract |
| 无谓复杂度 | 为“以后可能用”增加数据库、插件 ABI 或 trait | 删除投机式通用化 |

## 6. 实施节奏

- 非平凡任务先给出短计划：根因/不变量、受影响文件、局部还是结构性、实现方式、计划验证的行为接缝。
- 将逻辑上连续的修改成批完成，再验证。不要每改一个函数就运行格式化、静态检查、构建或全量测试。
- 实施中最多优先运行一个最贴近改动的 focused test，用它验证关键行为接缝。只有该测试暴露新信息时，才继续修改并重跑。
- 一个 coherent batch 完成后，再统一格式化并运行较宽的质量门禁；最终交付前只运行与风险相称的完整门禁一次。
- 不要把“多跑测试”当作补偿不清晰设计的手段。先缩小状态空间和重复逻辑，再验证。
- 仅在用户明确要求或上层规则允许时使用子代理；不要为了形式拆分任务。

## 7. 测试纪律：少而有效

### 7.1 禁止无用测试

- 禁止在每次小编辑后重复运行完整 `pytest`、全部 Vitest、Playwright、lint、typecheck、format check 或 build。
- 禁止在同一批修改中无理由重复运行等价门禁。`pnpm build` 已包含 `vue-tsc --noEmit`，不要紧接着重复 typecheck，除非需要分别定位失败或最终 CI 明确要求两者。
- 文档、注释、纯文案或不影响执行的样式 token 修改，不运行后端/前端业务测试；只做适用的格式或人工检查。
- 不为简单 getter、框架透传、静态类型已经保证的事实、私有实现细节或无分支映射新增测试。
- 不为了提高测试数量复制同一断言到 unit、integration 和 E2E。每一层只验证该层独有的契约。
- 不运行与改动无关的 E2E、Docker 服务、真实模型、真实文献源或 live 集成测试。
- 不为让测试通过而引入生产代码 fallback、mock success path、额外环境分支或第二套状态。

### 7.2 什么时候应新增或修改测试

仅在测试能保护下列高价值行为时增加或修改：

- bug 的最小复现和回归边界；
- canonical contract、状态 reducer、权限或校验不变量；
- 重试、幂等、租约、版本、持久完成状态；
- API 与前端、API 与 Worker 之间的序列化/恢复契约；
- 用户关键路径中已有测试无法覆盖的新分支。

优先修改离行为所有者最近的现有测试。只有没有合适归属或需要新的边界夹具时才新建测试文件。

### 7.3 开发阶段 focused test

从包目录运行一个最小目标：

```powershell
# backend/：单个测试函数或单个紧邻模块
uv run pytest tests/unit/test_example.py::test_specific_behavior -q

# frontend/：单个相关 Vitest 文件
pnpm exec vitest run tests/unit/example.test.ts

# 仅当改动触及真实用户流程时，运行一个相关 Playwright spec
pnpm exec playwright test tests/e2e/example.spec.ts
```

后端单元测试必须设置不超过 60 秒的硬超时。超时应作为失败调查，不得靠无限延长等待掩盖死锁、网络访问或错误 fixture。

## 8. 最终验证矩阵

验证强度由行为风险决定，而不是由改动文件数量决定。不要默认运行全仓门禁。

| 改动范围 | 开发中 | coherent batch 结束/最终验证 |
| --- | --- | --- |
| 文档、注释、纯文案 | 不跑业务测试 | 检查 diff、链接、命令和格式 |
| 后端局部纯逻辑 | 一个相关 pytest | 相关测试 + Ruff；类型边界变化再跑 Pyright |
| 后端共享 contract/状态/Worker | 一个 owner 附近测试 | 相关测试文件；最终一次 Ruff、Pyright，必要时完整 pytest |
| 前端局部 reducer/composable | 一个相关 Vitest | 相关 Vitest + lint/typecheck；避免重复 build |
| 用户可见页面/关键流程 | 一个 unit 或相关 E2E seam | format/lint/typecheck + 相关 E2E；生产边界变化时再 build |
| 前后端协议或跨进程状态 | 两侧各一个最小契约测试 | 受影响测试组；范围广时最终一次完整包门禁 |
| 基础设施/Compose | 配置解析 | `docker compose ... config --quiet`；非必要不启动全部服务 |

需要完整后端门禁时，从 `backend/` 运行一次：

```powershell
uv run ruff check app tests
uv run ruff format --check app tests
uv run pyright
uv run pytest tests -q
```

需要完整前端门禁时，从 `frontend/` 运行一次：

```powershell
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:e2e
pnpm build
```

完整前端门禁中的 typecheck 与 build 存在重复成本，只在最终 CI 对齐、发布验证或用户明确要求时两者都跑。普通局部任务按上表选择其一。

所有带 `RUN_LIVE_*` 的测试默认不运行。它们会访问真实模型、外部学术 API 或本地持久服务，只有以下条件同时成立时才运行：改动直接影响该边界、普通测试无法提供证据、所需服务/凭据已确认可用，且用户要求或验收范围明确包含 live 测试。不得把 live 测试加入常规开发循环。

如果某项验证无法运行，明确说明原因和已完成的次优检查，不伪造成功。

## 9. 项目架构不变量

### 9.1 允许的依赖方向

```text
api/routers -> api/deps ┐
workers ----------------┼-> modules ports/services
                        └-> infra adapters -> external systems
```

- `modules` 只包含业务用例、领域状态、命令、值对象和端口，不得导入 `api`、`workers` 或具体 `infra` 实现。
- `infra` 实现 PostgreSQL、Redis、arq、Milvus、对象存储、LLM、Reranker 和 checkpoint 端口，可以向内依赖业务 contract；业务模块不能反向依赖适配器。
- `api/routers` 只处理 HTTP/SSE 协议、鉴权、参数和领域错误映射。请求级依赖装配放在具名 `api/deps` 中，不建立通用 DI 容器。
- `workers` 只处理任务 payload 校验、依赖装配、重试边界和用例调用，不保存、复制或重新解释业务规则。
- 禁止 `modules -> workers`、业务模块双向导入、服务内部默认构造基础设施，以及数据库模型引用展示逻辑。

### 9.2 业务所有权

| 事实或用例 | 唯一所有者 |
| --- | --- |
| 工作区、研究计划、集合、会话 | `modules/research` |
| search run、相关性和候选审核 | `modules/search` |
| 引用、论文事实和准入 | `modules/literature` |
| 全文获取、文件和版本 | `modules/documents` |
| 解析、切块、嵌入、索引、检索和证据 | `modules/rag` |
| LangGraph 状态、图和节点 | `modules/agents` |

- 修改状态、事件、API payload、Redis key 或任务协议前，必须先标明事实所有者、持久化位置、生产者、消费者和刷新/重试恢复路径。
- Protocol 与业务 command 放在事实所有者模块；SQLAlchemy、Redis、arq、Boto3、Milvus 和模型客户端只在 `infra` 或 composition root 中出现。
- 跨模块调用使用所有者 facade、稳定 ID 或已提交事件；不得导入另一个模块的内部 service、repository 或展示 helper。

### 9.3 前端边界

- `views` 只负责路由参数和页面级组合；业务 UI、交互状态与格式化放在对应 `features`。
- TanStack Query hooks 与统一 query keys 放在 `api/hooks`；搜索和研究 SSE 恢复分别放在所属 feature composable。
- Pinia 只保存认证、输入草稿和展开状态等纯 UI 状态，不复制服务端查询结果或持久任务完成状态。
- OpenAPI 是前端请求与响应 DTO 的唯一来源。`api/generated` 由工具生成，禁止同时维护手写 TypeScript wire shape。

### 9.4 文件重量

- 文件长度是架构信号，不是拆分目标。普通业务源文件建议控制在 600 行以内；超过 700 行必须审查是否混入多个所有者或用例；超过 1000 行必须拆分。
- 生成代码、OpenAPI schema 和 Alembic migration 不受 1000 行上限约束，但不得手工编辑生成文件。
- 不得为凑行数机械拆出无所有权的 `utils`、`helpers` 或空转 facade。即使不足 1000 行，出现多事实来源、循环依赖、协议与实现混放或无法独立验证时仍必须拆分。

### 9.5 产品状态不变量

- 前端刷新后必须通过服务端 API 恢复工作区、计划、当前 search run 和候选状态；不得依赖仅存在于页面内存的完成标记。
- “正在查看”“Redis 准备清单”“PostgreSQL 待确认集合”“可用于 RAG 的集合”是不同状态，不得合并为一个布尔值或一套前端本地状态。
- 候选在满足服务端 DOI、题录、全文和权限准入前，不写入持久 `papers` 真相。
- 搜索、相关性分析和 ingestion Worker 使用各自队列与明确 owner；不要跨 Worker 偷读内部状态或复制完成判断。
- 事件、API payload 和前端类型只保留一个 canonical shape。改变 contract 时同步更新生产者、消费者和最近的契约测试。
- 外部来源失败必须显式呈现；不得静默切换 provider、直连/代理模式或伪造完整结果。
- PostgreSQL 中的 durable state 必须能够解释任务是否完成；Redis 和进程内存不能成为不可恢复的唯一完成证据。
- Alembic revision 一旦可能已应用就不得原地修改；schema 变化创建新 revision，并验证 upgrade 路径。

纯 `AGENT.md`、ADR 或其他文档规则修改只检查 diff、链接、命令和格式，不运行后端、前端或 E2E 业务测试。

## 10. 安全与数据完整性

- 不硬编码 secret、API key 或凭据；使用环境变量或 secret manager。
- 所有数据库访问使用 SQLAlchemy 参数绑定或等价参数化接口，不拼接外部输入生成 SQL/命令。
- 在 API、Worker 消息、文件和外部 provider 响应进入系统的边界进行校验。
- 破坏性数据库、Redis、MinIO、Milvus 或 Docker volume 操作必须先确认精确目标和恢复影响；不得把清理整个开发环境当作普通调试步骤。

## 11. 前端设计

- 本项目任何用户可见页面的设计、重构或美化，必须优先使用 `design-taste-frontend`：`C:\Users\yz\.agents\skills\design-taste-frontend\SKILL.md`。
- 不得使用 `frontend-design` 作为本项目的前端设计依据。
- 研究工作台、数据表格和多步骤流程只采用该 skill 的设计审计、视觉一致性、可访问性与反模板化原则；功能交互仍以现有产品流程、状态恢复和高密度操作效率为准。
- 用户可见交互变化必须用真实浏览器检查相关视口和关键状态；只启动开发服务器不构成验证。纯逻辑或不可见改动不做无关截图巡检。

## 12. 交付前检查

最终答复前只做一次 diff review，确认：

- 修的是根因，而不是给症状增加旁路；
- 没有重复逻辑、第二事实来源、隐藏 fallback、吞异常或无效默认值；
- 没有遗留被替代的 dead code 和未说明的行为变化；
- 测试数量与风险相称，且每个测试保护明确契约；
- 没有重复运行无关或等价门禁；
- API、状态恢复、安全和数据完整性没有退化。

当用户核心请求已有足够证据支持时立即停止，不继续搜索、扩写示例或追加非必要优化。
