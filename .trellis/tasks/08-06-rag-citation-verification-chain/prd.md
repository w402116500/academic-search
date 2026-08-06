# 修复 RAG 引用与核验链路

## Goal

让研究问答 RAG 链路使用已确认的三层证据身份模型，消除模型同时接触
`E1/E2` 与 UUID 后造成的 verifier 协议失败、卡住和引用错配问题。

用户价值是：RAG 回答要么输出可追溯、可点击、编号连续的引用；要么明确说明
当前证据不足，不能因为某个模型输出了错误 ID 而让整条问答链路停在不可理解的
状态。

## Background

当前实现中，回答模型和回答主张核验器仍通过 `cited_chunk_ids` 与
`supporting_chunk_ids` 输出 UUID。提示词同时展示 `[E1]` 与 `chunk_id=...`，
导致模型侧协议混合。已观察到 verifier 返回“看起来合法但不属于当前证据集”的
UUID，从而触发拒绝并让用户侧问答卡住。

已接受的设计文档：

- `docs/adr/0003-rag-evidence-identity-boundaries.md`
- `docs/adr/0004-conditional-rag-presentation-editing.md`
- `.trellis/spec/backend/rag-answer-citation-verification.md`
- `docs/05-rag-research-workspace-discussion.md` 中的 7.1 节

## Requirements

1. 模型侧只使用 `EvidenceRef`
   - 回答模型 prompt 只能展示 `[E1]`、`[E2]` 等短引用。
   - 回答模型结构化输出使用 `cited_refs`，不再要求输出 UUID。
   - 回答 claim 草稿需要携带 `claim_id`、`text` 和 `refs`，用于后续核验与修复。

2. Verifier 只输入/输出 `EvidenceRef`
   - 回答主张核验器只能输出 `supporting_refs`。
   - `supported=false, supporting_refs=[]` 是语义不支持，不是 ID 映射错误。
   - `E9`、UUID 或任何不存在于当前快照的 ref 都是协议错误。

3. 后端维护 Evidence Snapshot 映射
   - 每次 answer attempt 建立 `EvidenceRef -> chunk_id` 映射。
   - `EvidenceRef` 只在一次快照内有效，允许每轮从 `E1` 重新开始。
   - 实现可以先使用当前 `research_runs` / `retrieval_trace` 承载快照语义；如果需要持久化 schema，应保持 `snapshot_id + evidence_ref` 唯一的语义。

4. Unsupported claim 进入修复链路
   - verifier 发现不支持的 factual claim 时，不能字符串删除原答案片段。
   - Final Composer 负责基于 supported claims 重新组织答案。
   - 只要进入 composer 修复路径，修复后的答案必须再跑一次 verifier。

5. 用户侧引用按最终答案首次出现顺序编号
   - 前端/用户只看到 `[1] [2] [3]`。
   - 如果最终答案先引用 `E3` 再引用 `E1`，用户侧必须渲染为 `[1] -> E3`、`[2] -> E1`。
   - 未被最终答案引用的证据不展示，也不占号。

6. 失败 attempt 可诊断
   - 协议错误不能生成普通 assistant message。
   - 失败原因、快照和模型输出摘要必须能在 trace/持久状态中追踪。
   - 普通用户界面显示友好的失败/重试状态，不暴露内部 UUID 协议细节。

7. 全部支持回答的表达质量
   - 首轮 Writer 必须以自然中文直接回答短问题；连续事实共用同一证据时，在同一
     语义段末引用一次，而不是逐句重复引用。
   - 只有成功通过首轮 verifier、且命中 Citation Fragmentation Gate 的回答才进入
     Presentation Editor；其输入仅为问题和已支持主张/`EvidenceRef`，不含完整原文。
   - Presentation Editor 必须在 45 秒总预算内只尝试一次，并在输出后再次 verifier。
   - 编辑、协议或二次核验失败时，发布原始已核验回答；该回退不是澄清、失败或重试。
   - 在 `research_runs.retrieval_trace.presentation_quality` 保存门槛判断、首稿、编辑稿
     与回退原因；普通会话 API 不暴露这些内部内容。

## Out of Scope

- 不重构整套研究会话、消息或运行表结构。
- 不引入新的检索算法、reranker 或 embedding 策略。
- 不改变候选相关性批次隔离逻辑。
- 不恢复已删除的 Trellis 工作日志文件。
- 不把 `E1/E2` 暴露为最终用户引用编号。

## Acceptance Criteria

- [ ] 回答与 verifier prompt 不再包含 `chunk_id=` 或 UUID 文本。
- [ ] `AnswerDraft` 和回答主张核验契约使用 `cited_refs` / `supporting_refs`。
- [ ] 后端能把 `EvidenceRef` 稳定映射回当前检索证据的 `chunk_id`。
- [ ] `E9`、UUID 泄漏、空引用却声称 supported 等协议错误被拒绝并记录诊断。
- [ ] 全部 supported 的回答走直通路径，不进入 composer。
- [ ] 存在 unsupported claim 的回答进入 composer，并在 composer 后再次 verifier。
- [ ] 用户侧引用编号按最终答案首次引用顺序连续生成。
- [ ] API 返回的引用对象保留 `display_index`、`evidence_ref` 和真实 `chunk_id`。
- [ ] 单元测试覆盖直通、修复、协议错误、引用重排和旧 UUID 失败模式。
- [ ] 相关后端/前端类型检查和目标测试通过，或明确记录无法运行的环境原因。
- [ ] 短问题 Writer 提示词要求自然中文、直接回答与段末同源引用。
- [ ] 连续至少 3 个使用同一单一 `EvidenceRef` 的带引用句子触发 Presentation Editor；
  正常回答不增加模型调用。
- [ ] Presentation Editor 只接收已支持主张与 `EvidenceRef`，总预算 45 秒、无重试，
  并通过第二次 verifier 后才替换原始回答。
- [ ] 编辑超时、协议错误或二次 verifier 拒绝时保留原始已核验回答，并写入
  `presentation_edit_fallback` 审计记录。

## Open Questions

无。用户已确认：

- EvidenceSnapshot 挂在 answer attempt 上；
- 失败 answer attempt 保存诊断但不作为普通 assistant message 展示；
- composer 修复路径需要二次 verifier；
- 用户侧引用编号按最终答案首次出现顺序重排。
- Citation Fragmentation Gate 只使用可解释的引用重复结构，不自动评判中文文采；
- Presentation Editor 只使用 verifier 已支持的主张和引用，不读取完整证据片段；
- 编辑分支总预算为 45 秒、无重试，失败时回退原始已核验回答；
- 表达质量审计复用 `research_runs.retrieval_trace.presentation_quality`，不新建审计表。
