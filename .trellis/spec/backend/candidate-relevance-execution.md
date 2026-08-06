# Candidate Relevance Execution

## Scenario: Isolated Candidate-Relevance Batch Retry

### 1. Scope / Trigger

Use this contract when changing candidate-relevance model validation, claim
verification, Redis search snapshots, or the relevance Worker retry flow. One
malformed model item must not discard verified results for other candidates in
the same logical batch.

### 2. Signatures

```python
async def CandidateRelevanceEvaluator.assess(
    *,
    context: CandidateRelevanceContext,
    candidates: Sequence[UnifiedCandidate],
) -> CandidateRelevanceEvaluationOutcome: ...

async def CandidateRelevanceJobQueue.enqueue_relevance(
    *,
    search_run_id: UUID,
    attempt_no: int,
) -> str: ...
```

The queue signature is intentionally unchanged. Retry membership belongs to
the search-run snapshot, never to the arq job payload.

### 3. Contracts

`CandidateRelevanceEvaluationOutcome` has two disjoint sets:

- `resolved_candidates`: candidates with a completed assessment or a terminal
  exclusion;
- `retryable_failures`: candidate ID to code for results that cannot yet be
  trusted.

The private Redis snapshot key `relevance_retry_candidate_ids` is an ordered
JSON list of UUID strings. On attempt 2, the Worker uses the list only when
its IDs exactly match the currently `PENDING`, triage-included candidates
before calling the evaluator. An absent, empty, invalid, nonmatching, or
partially matching field that omits a pending ID or contains any foreign ID
falls back to the pending included set; it must never cause completed or
terminal candidates to be recalculated or leave pending candidates behind at
completion.

Attempt 1 continues to provide the complete triage-included collection to the
model so that candidates retain the shared comparison context. Snapshot merges
replace only `relevance_state`, `relevance_assessment`, and `relevance_error`.
When one outcome contains both resolved and retryable candidates, the relevance
field merge and retry-ID/attempt update must be committed in one
`merge_snapshot` transform before queueing attempt 2.

### 4. Validation And Error Matrix

| Condition | Evaluator outcome | Worker action |
| --- | --- | --- |
| Stream empty, truncated, or outer JSON invalid | `CandidateRelevanceTechnicalFailure` | Retry all currently unresolved candidates in this invocation once |
| Missing, duplicate, malformed, or evidence-invalid assessment item | Retry failure for that candidate | Merge valid peers, retry only failed IDs |
| Claim verifier item missing, malformed, duplicate, or temporarily unavailable | Retry failure for that candidate | Merge verified peers, retry only failed IDs |
| `candidate_relevance_claim_unsupported` | Terminal excluded candidate | Do not retry or display in review |
| No abstract | `insufficient_information` terminal exclusion | Do not call the model for a fabricated assessment or retry it |
| Retry exhausted | Terminal excluded candidate with stored error code | Exclude only IDs in the retry subset |
| Retry IDs empty, malformed, missing a pending ID, or containing any ID absent from pending candidates | Invalid private snapshot state | Fall back to pending included candidates |

### 5. Good / Base / Bad Cases

- Good: one `core` result and one evidence-invalid result return from one model
  call; persist `core`, then queue a single attempt containing only the invalid
  candidate ID.
- Base: every assessment item is valid; do not create
  `relevance_retry_candidate_ids` and continue to citation enrichment.
- Bad: treating one invalid item as a failure of the whole collection, then
  re-evaluating or excluding its valid peers.

### 6. Tests Required

- `backend/tests/unit/test_candidate_relevance.py` must cover valid plus
  malformed/missing/duplicate assessment items, empty arrays, claim-verifier
  isolation, and the terminal unsupported path.
- `backend/tests/unit/test_candidate_relevance_execution.py` must assert that
  a partial result is merged before retry, the snapshot stores UUID-string
  retry IDs in the same atomic merge, attempt 2 receives only those IDs (or
  safely falls back from an empty, nonmatching, or partially matching corrupt
  subset that omits or adds IDs), and exhaustion excludes only those candidates.
- Preserve `test_relevance_merge_only_replaces_relevance_fields` so citation,
  source-record, and selection data survive partial relevance updates.

### 7. Wrong Vs Correct

#### Wrong

```python
if outcome.retryable_failures:
    raise CandidateRelevanceTechnicalFailure("candidate_relevance_output_invalid", "...")
```

This discards `resolved_candidates` and schedules a whole-collection retry.

#### Correct

```python
await session_store.merge_snapshot(
    session_key,
    lambda current: merge_relevance(current, outcome.resolved_candidates),
)
await queue.enqueue_relevance(search_run_id=run.id, attempt_no=2)
```

Persist the unresolved UUID subset in the same retry-scheduling snapshot so
attempt 2 can derive its bounded model input without changing the queue API.
