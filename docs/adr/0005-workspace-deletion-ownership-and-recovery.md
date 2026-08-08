# ADR 0005: Workspace Deletion Ownership and Recovery

- Status: Accepted
- Date: 2026-08-08
- Related: [`0003-rag-evidence-identity-boundaries.md`](0003-rag-evidence-identity-boundaries.md)

## Context

Permanent workspace deletion removes PostgreSQL records as well as private
Milvus vectors and full-text objects. Research runs and their evidence are
private to a workspace, while their `RESTRICT` references to the input message
and source chunk protect ordinary audit data from accidental physical deletion.
The ORM currently cascades conversation messages and document chunks before the
dependent research records, so deleting only the workspace root violates those
intentional protections.

External cleanup can also fail or take time after the workspace has been fenced.
Treating that workspace as deleted would lose the recovery handle; hiding it
from the workspace list would make a page refresh look like data loss.

## Decision

- `ResearchRun` and its evidence are removed only as part of the dedicated
  permanent-workspace-deletion transaction. That transaction explicitly deletes
  the workspace's evidence first, then its runs, before deleting the workspace
  root.
- The input-message and evidence-to-chunk `RESTRICT` foreign keys remain in
  place for all ordinary maintenance and audit flows.
- Permanent deletion first fences the workspace as `deleting`, stops new work,
  waits for active work to reach a terminal state, and cleans private external
  resources. The root record is removed only after those steps succeed.
- A deletion-pending workspace remains visible to its owner but cannot be opened
  for research or edited. Repeating the same delete action resumes the
  idempotent cleanup.
- The UI uses a short generic deletion-incomplete message. Error codes, resource
  type, and the original exception remain backend diagnostics.

## Consequences

- The permanent deletion repository owns a narrowly scoped, tested delete order
  while ordinary audit flows retain database protection against accidental loss.
- A failed cleanup is recoverable without restoring an active workspace or
  exposing partially deleted research data.
- Research-run history is not retained after its owning workspace is deleted.
- Schema migration and integration coverage are required before this behavior
  can be considered complete.
