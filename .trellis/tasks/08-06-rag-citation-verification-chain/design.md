# 修复 RAG 引用与核验链路 - Design

## Architecture And Boundaries

目标是把 RAG 回答链路从“模型输出 UUID”迁移为“模型输出快照内
EvidenceRef，后端负责映射 UUID，前端只展示用户引用编号”。

边界如下：

```text
RAG retrieval
  -> EvidenceSnapshot in agent/service layer
  -> Answer Draft model contract: E-ref only
  -> Protocol validation
  -> Claim Verifier model contract: E-ref only
  -> Unsupported: Final Composer -> second Claim Verifier
  -> Supported + citation fragmentation: Presentation Editor -> second Claim Verifier
  -> Supported + no fragmentation: direct finalization
  -> E-ref -> chunk_id resolution
  -> E-ref -> user [n] rendering
  -> Research run/message persistence and API response
```

Ownership:

- `modules/rag` owns retrieved chunk facts and `chunk_id`.
- `modules/agents` owns model-facing prompt contracts, verifier/composer flow,
  and graph state transitions.
- `modules/research` owns run/conversation persistence ports and API-facing
  research outcome shape.
- `infra/llm` adapts structured model calls to those contracts.
- `api`/OpenAPI/frontend consume only final answer and reference DTOs.

## Data Flow

1. Retrieve evidence as existing `RetrievedEvidence` objects.
2. Build an in-memory/persistable `EvidenceSnapshot`:

   ```text
   E1 -> evidence[0].chunk_id
   E2 -> evidence[1].chunk_id
   ```

3. Prompt answer model with `[E1]` blocks only.
4. Validate answer draft:

   - every `cited_refs` item is in the snapshot;
   - every claim ref is in the snapshot;
   - `evidence_sufficient=true` requires at least one cited ref.

5. Verify claims using only cited evidence refs.
6. If every claim is supported, evaluate Citation Fragmentation Gate:

   - split only on Chinese terminal punctuation (`。！？`), preserving decimal values;
   - trigger only when at least three adjacent citation-bearing sentences each use the
     same single `EvidenceRef`;
   - if it does not trigger, finalize directly;
   - if it triggers, call Presentation Editor with the question and verifier-supported
     claims plus their `EvidenceRef` values only. The editor never receives the full
     evidence blocks or original draft as model input.

7. Presentation Editor has one attempt and a 45-second aggregate budget for editing
   and its second verifier. A timeout, model/protocol error, or unsupported editor
   output falls back to the original verified draft instead of failing the run.
8. If any original draft claim is unsupported, call Final Composer with:

   - original question;
   - draft answer;
   - verifier result;
   - supported claim text/ref pairs;
   - explicit instruction not to add new factual claims.

9. Re-verify composer output.
10. Parse the selected final answer's EvidenceRef order and render user citations:

   ```text
   final refs: E3, E1, E3
   display:    E3 -> [1], E1 -> [2]
   ```

11. Store/return references with display index, evidence ref and chunk UUID. Store
    presentation gate/editor diagnostics under `retrieval_trace.presentation_quality`;
    raw diagnostic answers stay server-side and do not enter normal message DTOs.

## Contracts

Target model contracts are defined in:

- `.trellis/spec/backend/rag-answer-citation-verification.md`

Implementation should update current code that still uses:

- `AnswerDraft.cited_chunk_ids`
- `EvidenceVerification.supported_chunk_ids`
- `AnswerClaimVerificationItem.supporting_chunk_ids`
- `evidence_prompt(... chunk_id=...)`

## Compatibility And Migration

This can be implemented without changing existing database tables if the first
iteration stores the EvidenceSnapshot mapping in run trace and final references.
If API schemas already expose `cited_chunk_ids`, keep backward compatibility only
as a server-derived field if required by existing consumers; do not ask models to
generate it.

Frontend display must continue to show regular citation numbers. If current UI
already displays references by array index, adapt the API/adapter layer so that
array order matches `display_index`.

## Error Handling

Protocol errors:

- unknown ref such as `E9`;
- UUID where `EvidenceRef` is required;
- supported claim with no refs;
- unsupported claim with refs;
- composer output with unknown refs.

These should fail the answer attempt with a stable failure code and diagnostic
trace, not publish an assistant answer.

Semantic unsupported:

- verifier returns `supported=false, supporting_refs=[]`.

This enters composer repair. If repair still fails verification, return a
bounded evidence-insufficient answer or clarification instead of publishing an
unsupported conclusion.

## Trade-offs

- Persisting a dedicated EvidenceSnapshot table is the clean long-term shape,
  but the MVP can preserve the same semantic contract in existing research run
  trace to avoid broad schema churn.
- A second verifier call only runs after composer repair. This protects quality
  without adding latency to all-supported answers. Presentation editing preserves
  this property by running only after a deterministic fragmentation trigger.
- E-ref values are deliberately not user-visible. This avoids jumpy `[3] [7]`
  citations and keeps UI citation order dense.
- Presentation Editor uses a closed verifier-approved claim set rather than the
  evidence blocks. This prevents a quality rewrite from adding valid-but-unasked
  details, while the second verifier still protects paraphrase correctness.
- A cosmetic-editor failure must not turn a verified answer into a clarification or
  research failure; the bounded fallback can publish the original verified draft.

## Rollback Shape

If implementation causes regressions, the smallest rollback is to revert the
agent/model contract changes and restore the previous UUID-based flow. The docs
and ADR should remain as the desired target unless the design itself is rejected.
