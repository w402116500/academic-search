# ADR 0003: RAG Evidence Identity Boundaries

- Status: Accepted
- Date: 2026-08-06
- Related: [`05-rag-research-workspace-discussion.md`](../05-rag-research-workspace-discussion.md), [`04-literature-results-and-citation-discussion.md`](../04-literature-results-and-citation-discussion.md)

## Background

RAG answer generation currently needs to connect retrieved document chunks,
model citations, verifier judgments, persisted diagnostics, and frontend
references. Exposing database UUIDs to answer and verifier models makes the
model-facing protocol ambiguous: the prompt can show both short `E1` labels and
UUID chunk IDs, and a model can return a syntactically valid UUID that does not
belong to the current evidence set. This failure mode is hard to explain to the
user and hard to debug after the fact.

## Decision

Use three separate identities for RAG evidence:

| Identity | Example | Audience | Scope |
| --- | --- | --- | --- |
| Database chunk ID | `7c8a...` UUID | Backend and database | Persistent document chunk identity |
| Evidence Ref | `E1`, `E2` | Answer model and verifier model | One `EvidenceSnapshot` |
| User Citation Index | `[1]`, `[2]` | Frontend and user | One final assistant answer |

Each answer attempt creates and persists an `EvidenceSnapshot` that maps
`EvidenceRef` values to chunk UUIDs. `EvidenceSnapshot` belongs to the answer
attempt, not to the whole conversation, so each turn can start again from `E1`.
Failed answer attempts are persisted with their snapshot and failure diagnosis,
but are not shown as ordinary assistant messages.

Answer and verifier model contracts must use `cited_refs` and
`supporting_refs`; they must not receive or return UUIDs. The verifier only
judges whether claims are supported. If any claim is unsupported, a final
composer rewrites the answer using supported claims and explicit
evidence-insufficient statements, then the repaired answer is verified again.
The backend renders user citation numbers from the final answer's first-use
order and maps those display numbers back through `EvidenceRef` to chunk UUIDs.

## Consequences

- The model protocol becomes small and local, reducing UUID hallucination and
  cross-snapshot ambiguity.
- Debugging can replay the exact evidence set, model refs, verifier output, and
  failure reason for one answer attempt.
- User citations remain dense and readable (`[1] [2]`) even when the final
  answer cites `E3` before `E1`.
- Implementation must update schemas, prompts, persistence, API references, and
  frontend rendering together; partial migration would recreate the ambiguous
  mixed-ID protocol.
