# Implementation Plan

## Steps

1. Add a private typed result/outcome in
   `backend/app/modules/search/relevance.py` that separates resolved candidates
   from retryable candidate failures.
2. Change evaluator validation and claim-verification handling to populate that
   outcome, preserving terminal handling for unsupported claims and existing
   evidence checks.
3. Update `backend/app/modules/search/relevance_execution.py` to merge resolved
   candidates before retry, persist the retry candidate-ID subset, and select
   that subset on attempt 2.
4. Replace broad pending-candidate exclusion after an exhausted retry with
   subset-targeted exclusion.
5. Extend focused tests in
   `backend/tests/unit/test_candidate_relevance.py` and
   `backend/tests/unit/test_candidate_relevance_execution.py` for mixed-valid
   batches, parse failure, corrupt retry-subset fallback, exhausted retry, and
   preservation of existing candidate fields.
6. Run focused tests, then backend Ruff, format check, Pyright, import-linter,
   source-size check, and the backend test suite.

## Risk Controls

- Keep `CandidateRelevanceJobQueue.enqueue_relevance` and
  `run_candidate_relevance` signatures unchanged.
- Preserve the existing ownership split: domain evaluator owns assessment
  validity; executor owns snapshot, retry, and terminal state transitions.
- Do not add JSON repair, regenerated reasons, or user-visible failure states.
- Verify all snapshot transformations preserve non-relevance fields.

## Rollback

Revert the evaluator outcome and retry-subset handling together. The optional
snapshot field is harmless to the existing worker and requires no migration.

## Commit Plan

This plan is intentionally pending user confirmation; it does not authorize a
commit by itself.

1. `fix: 隔离候选相关性批次失败`
   - `backend/app/modules/search/relevance.py`
   - `backend/app/modules/search/relevance_execution.py`
   - `backend/tests/unit/test_candidate_relevance.py`
   - `backend/tests/unit/test_candidate_relevance_execution.py`
   - `backend/tests/integration/test_live_candidate_relevance.py`
   - Includes the private outcome contract, atomic partial-result merge, one
     bounded retry for only unresolved candidates, corrupt retry-subset fallback,
     and regression coverage.

2. `docs: 记录候选相关性批次隔离`
   - `docs/03-literature-search-and-discovery-discussion.md`
   - `docs/04-literature-results-and-citation-discussion.md`
   - `docs/06-session-reliability-and-governance-discussion.md`
   - `docs/08-development-environment.md`
   - `docs/11-implementation-alignment-discussion.md`
   - `docs/adr/0002-candidate-relevance-batch-boundaries.md`
   - `.trellis/spec/backend/candidate-relevance-execution.md`
   - `.trellis/tasks/08-05-candidate-relevance-batch-isolation/`
   - Records the product decision, implementation boundary, executable retry
     contract, task evidence, and this plan.

Stage the task paths explicitly; never use `git add .trellis`. Do not include
the separate bootstrap assets (`.agents/`, `.codegraph/`, `.codex/`,
`.gitattributes`, `AGENTS.md`, `CONTEXT.md`, `.trellis/agents/`,
`.trellis/scripts/`, `.trellis/workspace/`, `.trellis/tasks/00-bootstrap-guidelines/`,
or base `.trellis` configuration), or the deleted `findings.md`, `progress.md`,
and `task_plan.md`, in either commit. The separate `00-bootstrap-guidelines`
task owns those artifacts.
