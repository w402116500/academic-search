# 修复 RAG 引用与核验链路 - Implementation Plan

## Pre-Development Context

Read before code edits:

- `.trellis/spec/backend/index.md`
- `.trellis/spec/backend/rag-answer-citation-verification.md`
- `.trellis/spec/backend/directory-structure.md`
- `.trellis/spec/backend/database-guidelines.md` if persistence/schema changes are needed
- `.trellis/spec/backend/error-handling.md`
- `.trellis/spec/backend/quality-guidelines.md`
- `.trellis/spec/frontend/index.md` and relevant frontend specs if API/UI contracts change
- `.trellis/spec/guides/cross-layer-thinking-guide.md`
- `.trellis/spec/guides/code-reuse-thinking-guide.md`

## Ordered Checklist

1. Inspect current contracts and call graph.
   - `backend/app/modules/agents/contracts.py`
   - `backend/app/modules/agents/prompts.py`
   - `backend/app/infra/llm/research_model.py`
   - `backend/app/modules/agents/nodes/single_rag.py`
   - `backend/app/modules/agents/graph.py`
   - research persistence/API/frontend reference DTOs

2. Add EvidenceRef utilities.
   - regex/type validation for `E1` style refs;
   - snapshot builder for `RetrievedEvidence` sequences;
   - helper to resolve refs to evidence/chunk IDs;
   - helper to render final answer citations by first-use order.

3. Update model contracts.
   - replace `cited_chunk_ids` with `cited_refs`;
   - replace verifier `supporting_chunk_ids` with `supporting_refs`;
   - add draft claim structures and final composer output schema.

4. Update prompts.
   - remove `chunk_id=` from evidence prompt;
   - answer prompt asks for `cited_refs` and claim refs;
   - verifier prompt asks for `supporting_refs`;
   - add final composer prompt.

5. Update LLM adapter validation.
   - validate refs against current snapshot;
   - reject UUID leakage and unknown refs with stable `ResearchModelError`;
   - add `compose_final_answer` method if needed.

6. Update single-RAG graph.
   - build snapshot after retrieval;
   - store cited refs / final refs in state;
   - all-supported path finalizes directly;
   - unsupported path calls composer and second verifier;
   - final path resolves refs to chunk IDs and display citations.

6a. Add conditional presentation editing to all-supported finalization.
   - keep the first Writer prompt natural and question-first;
   - add a pure Citation Fragmentation Gate based on adjacent cited sentences;
   - add a Presentation Editor contract using only verifier-supported claims and refs;
   - allow one bounded edit plus second verifier within a 45-second aggregate deadline;
   - on editor timeout, protocol failure, model failure, or rejected second verifier,
     render the original verified answer and record `presentation_edit_fallback`;
   - persist server-only diagnostics below `retrieval_trace.presentation_quality`.

7. Update research outcome/API reference mapping.
   - preserve real `chunk_id` in persisted references;
   - return/display dense `[n]` citation indexes;
   - avoid exposing `E1/E2` as user labels.

8. Tests.
   - prompt tests: no UUID/chunk_id in model-facing evidence;
   - schema/validation tests: `E9`, UUID leakage, verdict/ref mismatch;
   - graph tests: all-supported, repair + second verifier, protocol failure;
   - display mapping tests: `E3` first becomes `[1]`;
   - regression for verifier returning out-of-set UUID.
   - fragmentation-gate tests: three adjacent single-ref cited sentences trigger;
     normal grouped prose and decimal values such as `p<0.001` do not mis-segment;
   - presentation-editor tests: closed claim-only input, success + second verifier,
     timeout/protocol/rejected-output fallback, 45-second aggregate budget, and
     internal trace without normal API exposure.

9. Quality checks.
   - targeted backend unit tests;
   - frontend type/test checks if frontend/API code changes;
   - project lint/type checks in proportion to changed scope.

## Risky Files / Rollback Points

- `backend/app/modules/agents/contracts.py`
- `backend/app/modules/agents/prompts.py`
- `backend/app/infra/llm/research_model.py`
- `backend/app/modules/agents/nodes/single_rag.py`
- `backend/app/modules/agents/graph.py`
- research persistence repository and OpenAPI schemas if final references change
- frontend conversation/reference rendering if API shape changes

Rollback point after steps 2-5: contracts and prompt tests should pass before
graph/persistence changes proceed.

## Validation Commands

Use the precise commands discovered from backend/frontend specs. Expected
minimum:

```powershell
uv run --directory backend pytest backend/tests/unit -q
uv run --directory backend ruff check
```

If frontend contract/rendering changes:

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend test -- --run
```
