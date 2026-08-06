# ADR 0002: Candidate Relevance Batch Boundaries

- Status: Accepted
- Date: 2026-08-06
- Related: [`03-literature-search-and-discovery-discussion.md`](../03-literature-search-and-discovery-discussion.md), [`11-implementation-alignment-discussion.md`](../11-implementation-alignment-discussion.md)

## Background

Candidate relevance needs shared comparison context across the eligible search
collection, but one malformed JSON item, invalid evidence quote, or temporarily
unavailable claim verification must not discard trustworthy peer results.

## Decision

Candidate relevance uses one logical batch for all eligible candidates when the
selected model's actual input and reserved output limits allow it. When those
limits would be exceeded, the flow uses bounded batches rather than
per-candidate calls.

For one logical batch, the first model invocation always receives the complete
eligible collection and returns candidate-ID-addressed JSON. The server
validates each returned item independently: verified candidates are persisted,
while only unresolved candidates receive one bounded retry as a collection.
An empty, malformed, incomplete, or foreign retry-ID set is corrupt private
snapshot state and falls back to every currently pending candidate, so no
candidate can silently remain pending when the run completes. No public API or
queue payload carries retry membership.

## Consequences

- Valid peers remain available when one candidate cannot produce a trusted
  assessment.
- Retry cost is bounded to one batch of unresolved candidates, never one model
  call per candidate.
- `not_recommended`, `insufficient_information`, unsupported claims, and
  exhausted technical failures are terminal internal outcomes and do not enter
  user screening.
