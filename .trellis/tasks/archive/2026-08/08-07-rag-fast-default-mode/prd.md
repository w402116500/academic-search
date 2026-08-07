# RAG 默认快速问答模式

## Goal

把 RAG 用户问答的默认体验从重型研究审计链路调整为快速引用问答链路，减少入口模型路由、逐 claim verifier、repair 与展示编辑带来的等待时间，同时保留引用身份映射和展示校验，确保答案引用仍可回溯到本轮证据快照。

用户价值：

- 普通文献问答更快返回，不再因为默认进入严格核验链路而长时间停留在 preparing。
- 用户可以显式切换到深度研究，以换取更完整的多论文综合和逐 claim 审计。
- 灰区问题默认先快答，失败时给出可理解的升档建议，而不是偷偷进入慢链路。

## Background

- 现有 academic-search RAG 链路在 `ResearchGraphRunner.run()` 入口先调用模型 `route_question()` 判断 single RAG / multi-agent；近期真实运行显示 `preparing` 可达到约 132 秒，检索本身多为 1-10 秒。
- 现有 single RAG 还会在回答后执行 `verify_answer_claims`，必要时 `repair_answer` 后再次验证，并可能做 presentation edit；该链路严谨但不适合作为所有普通问题的默认路径。
- 参考项目 `E:\mystudy\agent_study\SuperMew-tutorial-verify` 使用“默认快速、复杂才升档”的思路：简单问题通过本地规则跳过复杂度模型，复杂问题才进入子问题规划；其测试明确固定“明显简单问题不调用复杂度模型”。

## Requirements

- R1：新增 RAG 模式语义，至少包含快速问答与深度研究，并支持默认/自动入口。
- R2：默认用户问答优先走 Fast RAG；用户显式选择深度研究时走 Strict Research。
- R3：自动判断只在强复杂意图命中时选择 Strict Research；灰区问题必须默认 Fast RAG。
- R4：Fast RAG 不默认调用 LLM claim verifier / repair / second verify / presentation edit。
- R5：Fast RAG 必须继续执行确定性引用协议校验：正文 EvidenceRef、结构化 `cited_refs`、本轮 evidence snapshot、真实 chunk UUID、用户展示引用必须一致。
- R6：Fast RAG 证据不足或答案模型声明证据不足时，不自动升级 Strict Research；返回澄清/证据不足终态，并在 trace 中提示可切换深度研究。
- R7：前端提供“快速问答 / 深度研究”显式模式选择，默认快速问答；发送请求时把用户选择传给后端。
- R8：运行 trace 能区分 `fast_rag` 与 `strict_research`，并体现自动/用户选择来源、是否跳过 claim verifier、失败时建议的下一模式。
- R9：现有 Strict Research 行为保持可用，继续承载多论文比较、综合、冲突核验、逐条验证等高严谨任务。
- R10：新增测试锁定性能边界：简单/灰区默认不调用 `route_question()` 和 `verify_answer_claims()`；显式 strict 或强复杂意图才进入严格链路。

## Acceptance Criteria

- [ ] 默认提交普通 RAG 问题时，后端走 Fast RAG，不调用 `route_question()`。
- [ ] Fast RAG 成功回答时，返回可展示引用 `[1]/[2]`，且引用映射来自真实 chunk UUID。
- [ ] Fast RAG 不调用 `verify_answer_claims()`、`compose_final_answer()` 或 presentation edit。
- [ ] 用户选择深度研究时，后端走现有 Strict Research 链路。
- [ ] 自动模式下，包含明确比较/对比/综合/冲突核验/逐条验证等强复杂意图的问题进入 Strict Research；其他问题进入 Fast RAG。
- [ ] Fast RAG 证据不足时返回澄清/证据不足状态，不自动升级 Strict Research，并在 trace 中记录 `suggested_next_mode=strict_research`。
- [ ] 前端输入区默认显示快速问答模式，并允许用户切换深度研究。
- [ ] 后端单元测试覆盖路由、Fast RAG 成功、Fast RAG 证据不足、Strict Research 兼容。
- [ ] 前端类型检查/相关测试通过，后端 lint/type/test 至少覆盖受影响模块。

## Out Of Scope

- 不调整 embedding、Milvus/Postgres 检索算法本身。
- 不删除现有 Strict Research、claim verifier、repair 或 citation verification 能力。
- 不引入新的外部模型供应商。
- 不读取、打印或复述 `.env`、Cookie、LocalStorage、测试账户密码或其他凭据。

## Key Decisions

- 默认链路为 Fast RAG。
- Fast RAG 默认跳过完整 LLM claim verifier，只保留确定性引用协议校验。
- 灰区默认 Fast RAG。
- 前端提供显式模式选择，默认快速问答。
- Fast RAG 失败不自动升档，只提示用户可切换深度研究。

## Open Questions

无阻塞问题。实现过程中若发现 API/状态模型存在不可兼容约束，回到本文件更新范围后再继续。
