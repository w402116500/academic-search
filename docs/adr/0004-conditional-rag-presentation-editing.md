# ADR 0004: Conditional RAG Presentation Editing

- Status: Accepted
- Date: 2026-08-06
- Related: [`0003-rag-evidence-identity-boundaries.md`](0003-rag-evidence-identity-boundaries.md)

## Context

An answer can pass every evidence check yet read as a mechanical list because the
Writer puts the same `EvidenceRef` after each short fact. Running a general
composer for every supported answer would add two model calls and make a cosmetic
failure block an otherwise correct answer.

## Decision

Keep the short-answer Writer question-first and natural by default. After an
all-supported verifier result, apply a deterministic Citation Fragmentation Gate:
three adjacent citation-bearing sentences with the same single `EvidenceRef`
allow one Presentation Editor attempt. The editor receives only the question and
verifier-supported claims/refs, then undergoes a second verifier within a
45-second aggregate budget and without retry. Any timeout, protocol/model error,
or rejected edit publishes the original verified answer and records an internal
`presentation_edit_fallback` under `research_runs.retrieval_trace.presentation_quality`.

## Consequences

- Normal supported answers retain the current one-Writer, one-Verifier latency.
- The two-editor roles stay distinct: Final Composer repairs unsupported facts;
  Presentation Editor improves only already-supported prose.
- Diagnostics can compare the original and edited answers without exposing raw
  model-stage content through ordinary conversation APIs.
