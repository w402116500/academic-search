# Candidate Relevance Execution

## Scenario: Isolated Candidate-Relevance Batch Retry

### 1. Scope / Trigger

Use this contract when changing candidate-relevance model validation, claim
verification, persistent candidate rows, or the relevance Worker retry flow.
One malformed model item must not discard verified results for other candidates
in the same logical batch.

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

class SearchCandidateRepository(Protocol):
    async def list_candidates(self, *, search_run_id: UUID) -> tuple[UnifiedCandidate, ...]: ...
    async def update_relevance_and_schedule_retry(
        self,
        *,
        search_run_id: UUID,
        resolved_candidates: Sequence[UnifiedCandidate],
        retry_attempt_no: int,
        retry_candidate_ids: Sequence[UUID],
    ) -> None: ...
    async def relevance_retry_candidate_ids(
        self, *, search_run_id: UUID, attempt_no: int
    ) -> frozenset[UUID] | None: ...
    async def clear_relevance_retry(self, *, search_run_id: UUID) -> None: ...
```

The queue signature is intentionally unchanged. Retry membership belongs to
durable candidate rows, never to the arq job payload or a Redis snapshot.

### 3. Contracts

`CandidateRelevanceEvaluationOutcome` has two disjoint sets:

- `resolved_candidates`: candidates with a completed assessment or a terminal
  exclusion;
- `retryable_failures`: `Mapping[UUID, CandidateRelevanceCandidateFailure]`
  for results that cannot yet be trusted; persist the failure object's `.code`
  only when that candidate reaches terminal exclusion.

`SearchRunCandidate.relevance_retry_attempt_no` stores retry membership for the
next relevance attempt. `SearchCandidateRepository.relevance_retry_candidate_ids`
returns the candidate IDs whose stored retry attempt equals the arq attempt
number. On attempt 2 and later, the Worker uses that set only when it exactly
matches the currently `PENDING`, triage-included candidates selected for retry
before calling the evaluator. An absent, empty, nonmatching, or partially
matching set that omits a pending ID or contains any foreign ID falls back to
the pending included set; it must never cause completed or terminal candidates
to be recalculated or leave pending candidates behind at completion.

Attempt 1 continues to provide the complete triage-included collection to the
model so that candidates retain the shared comparison context. Relevance
updates replace only `relevance_state`, `relevance_assessment`, and
`relevance_error` on candidate rows. When one outcome contains both resolved
and retryable candidates, `update_relevance_and_schedule_retry()` must commit
the resolved relevance fields and the retry candidate membership in one
database transaction before queueing attempt 2.

### 4. Validation And Error Matrix

| Condition                                                                                             | Evaluator outcome                                  | Worker action                                                     |
| ----------------------------------------------------------------------------------------------------- | -------------------------------------------------- | ----------------------------------------------------------------- |
| Stream empty, truncated, or outer JSON invalid                                                        | `CandidateRelevanceTechnicalFailure`               | Retry all currently unresolved candidates in this invocation once |
| Missing, duplicate, malformed, or evidence-invalid assessment item                                    | Retry failure for that candidate                   | Merge valid peers, retry only failed IDs                          |
| Claim verifier item missing, malformed, duplicate, or temporarily unavailable                         | Retry failure for that candidate                   | Merge verified peers, retry only failed IDs                       |
| `candidate_relevance_claim_unsupported`                                                               | Terminal excluded candidate                        | Do not retry or display in review                                 |
| No abstract                                                                                           | `insufficient_information` terminal exclusion      | Do not call the model for a fabricated assessment or retry it     |
| Retry exhausted                                                                                       | Terminal excluded candidate with stored error code | Exclude only IDs in the retry subset                              |
| Retry IDs absent, empty, missing a pending ID, or containing any ID absent from pending candidates   | Invalid durable retry membership                   | Fall back to pending included candidates                          |

### 5. Good / Base / Bad Cases

- Good: one `core` result and one evidence-invalid result return from one model
  call; persist `core`, then queue a single attempt containing only the invalid
  candidate ID.
- Base: every assessment item is valid; do not create
  `relevance_retry_attempt_no` markers and continue to citation enrichment.
- Bad: treating one invalid item as a failure of the whole collection, then
  re-evaluating or excluding its valid peers.

### 6. Tests Required

- `backend/tests/unit/test_candidate_relevance.py` must cover valid plus
  malformed/missing/duplicate assessment items, empty arrays, claim-verifier
  isolation, and the terminal unsupported path.
- `backend/tests/unit/test_candidate_relevance_execution.py` must assert that
  a partial result is merged before retry, the database stores retry membership
  in the same atomic update, attempt 2 receives only those IDs (or safely falls
  back from an empty, nonmatching, or partially matching subset that omits or
  adds IDs), and exhaustion excludes only those candidates.
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
retry_candidate_ids = tuple(outcome.retryable_failures)
next_attempt_no = attempt_no + 1
await candidate_repository.update_relevance_and_schedule_retry(
    search_run_id=run.id,
    resolved_candidates=outcome.resolved_candidates,
    retry_attempt_no=next_attempt_no,
    retry_candidate_ids=retry_candidate_ids,
)
await queue.enqueue_relevance(search_run_id=run.id, attempt_no=next_attempt_no)
```

This single transaction persists valid results and the unresolved UUID subset
before attempt 2 can derive its bounded model input, without changing the queue
API.
