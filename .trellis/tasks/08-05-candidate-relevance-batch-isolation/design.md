# Technical Design

## Boundary

This pilot changes the private backend boundary between
`CandidateRelevanceEvaluator` and `CandidateRelevanceRunExecutor`. The
evaluator will distinguish resolved candidates from retryable candidate IDs
instead of turning any candidate-level validation error into a batch-wide
exception. The worker remains the owner of snapshot merging, queueing, retry
attempts, and terminal exclusion.

## Outcome Contract

Introduce a private, typed evaluation outcome that carries:

- resolved `UnifiedCandidate` instances with completed or terminal exclusion
  relevance fields;
- retryable failures keyed by candidate ID; and
- a batch-wide failure representation only when no response can be parsed.

The evaluator must continue to validate model output against the supplied
candidate IDs and evidence. A valid assessment proceeds through claim
verification. A claim-verifier rejection with
`candidate_relevance_claim_unsupported` is resolved as a terminal exclusion;
technical verification failures are retryable only for the candidates awaiting
that verification.

## Retry State

The Redis search snapshot gains a private retry-subset field containing the
candidate IDs to process on attempt 2. Attempt 1 evaluates all eligible
pending candidates. Attempt 2 uses this subset only when its IDs exactly match
the currently pending, included candidates; a missing or foreign ID is corrupt
private state and safely falls back to every pending candidate. A valid subset
never includes resolved peers.

The arq port and worker function remain `(search_run_id, attempt_no)`. This
keeps queue identity and API contracts stable while allowing the worker to
derive the subset from its own snapshot.

## Execution Flow

```text
eligible pending candidates
  -> one DeepSeek JSON-mode evaluation
  -> resolved candidates merge into snapshot
  -> retryable IDs stored in snapshot
  -> attempt 2 evaluates only stored IDs
  -> unresolved IDs after attempt 2 receive terminal exclusion
  -> citation enrichment runs over the final merged candidates
```

If an entire JSON response cannot be parsed, the current invocation's eligible
candidates become the retry subset. If a parsed response has only some bad
items, only those items are retried. Existing merge behavior must remain
field-scoped so concurrent citation and selection writes survive.

## Compatibility And Rollback

No persisted database schema or public DTO changes are required. The extra
Redis snapshot field is optional: absent data means attempt 1 semantics. A
rollback to the old code ignores the field and retains current safe exclusion
behavior. The first release is protected by focused unit tests rather than a
feature flag because it does not widen user-visible eligibility.

## Deferred Design

Evidence-bound per-field output, DeepSeek strict-schema capability handling,
batch-size estimation, total request deadlines, and user-visible status
changes remain outside this pilot.
