# Candidate Relevance Batch Isolation Pilot

## Goal

Prevent one invalid candidate-relevance result in a DeepSeek JSON-mode batch
from discarding valid results for its peers. The user should receive every
candidate with a trustworthy positive assessment while unreliable candidates
are retried once or excluded without appearing in review.

## Confirmed Facts

- The search worker normalizes, deduplicates, and triages all provider results
  before publishing the candidate snapshot (`backend/app/modules/search/execution.py:159`).
- The relevance worker currently sends one complete included-candidate
  collection to the evaluator (`backend/app/modules/search/relevance_execution.py:131`).
- The evaluator collects a complete streamed JSON object, validates every
  candidate result, and raises a batch-level technical failure if any item is
  missing, duplicated, or has unverifiable evidence
  (`backend/app/modules/search/relevance.py:548`,
  `backend/app/modules/search/relevance.py:600`).
- A first technical failure currently queues one complete-collection retry;
  after the second failure every pending candidate is excluded
  (`backend/app/modules/search/relevance_execution.py:139`,
  `backend/app/modules/search/relevance_execution.py:318`).
- Candidate review exposes only positive, verified relevance candidates through
  `is_screening_candidate` (`backend/app/modules/search/relevance.py:763`,
  `backend/app/modules/search/review_query.py:71`).

## Requirements

### R1: Preserve Valid Peer Results

When a parsed batch contains both valid and invalid candidate assessments, the
system must persist every valid, verified result and must not recalculate or
overwrite it because another candidate is invalid.

### R2: Retry Only Unresolved Candidates

The first retry must process only candidates whose assessment could not be
produced or validated. It remains one bounded batch invocation, not one model
call per candidate. If the whole JSON response is empty, truncated, or
unparseable, every candidate in that invocation is unresolved and may be in
the retry subset.

### R3: Keep Terminal Outcomes Terminal

`not_recommended`, `insufficient_information`, and `unsupported` remain
non-retryable exclusions. A candidate that remains technically unresolved
after its one retry is excluded from user-facing review while its internal
error code remains available for audit.

### R4: Preserve Existing External Contracts

Do not change the HTTP API, frontend behavior, DeepSeek prompt, response
schema, model adapter, or arq job function signature in this pilot. The
existing queue continues to receive only a search-run ID and attempt number.

### R5: Keep State Merges Non-Destructive

Partial relevance updates must preserve candidate metadata, citation data,
full-text state, and existing user selection data exactly as the current
snapshot merge does.

## Acceptance Criteria

- [x] Given two eligible candidates and a parsed response where one passes all
  validation and one does not, the valid candidate is stored as completed and
  only the invalid candidate is scheduled for attempt 2.
- [x] Given an empty, truncated, or otherwise unparseable response, the retry
  subset contains all unresolved candidates in that invocation; completed
  candidates from an earlier partial result are not included.
- [x] Given a retry subset with one candidate and an exhausted retry, only
  that candidate becomes excluded; completed and terminal peers are unchanged.
- [x] Given a retry snapshot that omits a pending candidate or contains a
  foreign candidate ID, attempt 2 falls back to every currently pending,
  included candidate and cannot complete with an orphaned pending candidate.
- [x] `candidate_relevance_claim_unsupported` remains terminal and does not
  enter the retry subset.
- [x] Existing snapshot merge guarantees for citation, source records, and
  selection data continue to pass.
- [x] Focused backend unit tests cover the new reducer/outcome and execution
  retry behavior without live DeepSeek, Redis, or HTTP dependencies.

## Out Of Scope

- Prompt wording, evidence-bound response-schema changes, DeepSeek JSON-mode
  adapter changes, and provider capability changes.
- Dynamic batch sizing beyond the accepted ADR policy.
- API/OpenAPI, frontend, review-page, manual-retry, or database migration work.
- Displaying `temporarily_unavailable`, `unsupported`, or
  `insufficient_information` candidates to end users.

## Resolved Decisions

- Use one logical complete batch when model input and reserved output limits
  allow it; split only when those actual limits require it.
- Candidates without an abstract are excluded before user review and do not
  provide user value.
- `background` is a verified, user-visible positive result ordered after
  `core` and `related`.
- Unsupported reasons are terminal, not corrected or regenerated in this
  pilot; they are retained as an internal prompt-quality signal.
