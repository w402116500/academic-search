# Research Run Lifecycle

## Scenario: Cancellation And Worker Restart Recovery

### 1. Scope / Trigger

Use this contract when changing research question queueing, `ResearchRun`
status transitions, cancellation, Worker startup, terminal SSE publication, or
frontend pending-run recovery. The invariant is that PostgreSQL is the durable
source of truth; Redis Stream events are replay hints and cannot be the only
way a run reaches a terminal state.

### 2. Signatures

Durable run fields:

```text
ResearchRun
  status = queued | running | awaiting_clarification | completed | failed | cancelled
  stage = dispatch | preparing | ... | completed | awaiting_clarification | failed | cancelled
  cancel_requested_at: datetime | None
  output_message_id: UUID | None
  retrieval_trace.cancellation.state = requested | confirmed
```

Execution adapter recovery entry:

```python
async def finalize_requested_cancellations(self) -> tuple[UUID, ...]: ...
```

Worker startup must call the recovery entry after dependency construction and
publish a terminal `ResearchProgressEvent(status=cancelled, stage=cancelled)`
for each recovered run on a best-effort basis.

### 3. Contracts

Queued cancellation is immediate: set `status=cancelled`, `stage=cancelled`,
`cancel_requested_at`, and `finished_at`.

Running cancellation is cooperative at request time: set `cancel_requested_at`
and `retrieval_trace.cancellation.state="requested"` while leaving
`status=running`. The in-flight Worker should finalize the run at the next safe
boundary.

Worker restart recovery must finalize any `running` run that already has
`cancel_requested_at` set. This prevents a process restart, model timeout, or
lost arq in-flight job from leaving the frontend permanently blocked by
`running/preparing` with no output message.

Completion and failure persistence must continue to treat
`cancel_requested_at` as stronger than a late answer or exception: if the run
has a cancellation request, mark it `cancelled` and do not create an assistant
message or evidence records.

### 4. Validation & Error Matrix

| Condition | Action |
| --- | --- |
| `queued` run is cancelled | Persist `cancelled` immediately |
| `running` run receives first cancel request | Persist `cancel_requested_at`; keep `running` until Worker boundary or restart recovery |
| Worker reaches a safe boundary after cancellation | `finalize_cancellation()` closes the active timing stage and marks `cancelled` |
| Worker starts and finds `running + cancel_requested_at` | `finalize_requested_cancellations()` marks `cancelled` and emits terminal SSE best-effort |
| Late `complete()` sees `cancel_requested_at` | Mark `cancelled`; do not write assistant message/evidence |
| Late `fail()` sees `cancel_requested_at` | Mark `cancelled`; do not write failed error as final user-visible state |
| Redis terminal event publish fails during recovery | Keep PostgreSQL terminal state; log safe diagnostics only |

### 5. Good / Base / Bad Cases

- Good: user cancels a long strict research call, the Worker is restarted, and
  startup recovery turns the run into `cancelled` so the composer is unlocked.
- Base: user cancels while the Worker is healthy; the next stage boundary
  confirms cancellation and publishes a terminal SSE event.
- Base: a cancelled run's late model result returns after the DB terminal state;
  the late `complete()` path is ignored or converts to `cancelled`, never an
  answer.
- Bad: relying only on Redis Stream terminal events; a Worker restart can lose
  the event while PostgreSQL still says `running`.
- Bad: leaving `running + cancel_requested_at + output_message_id=null`
  indefinitely; the frontend treats it as a pending run and disables new
  questions.

### 6. Tests Required

- Unit-test `finalize_cancellation()` closes the active stage and writes
  `retrieval_trace.cancellation.state="confirmed"`.
- Unit-test `finalize_requested_cancellations()` recovers
  `running + cancel_requested_at` runs after an interrupted Worker.
- Unit-test `complete()` / `fail()` do not publish answers or failed terminal
  state when a cancellation request exists.
- Browser or API regression should verify there are no stale
  `queued` / `running` research runs after restarting the Research Worker.

### 7. Wrong Vs Correct

#### Wrong

```python
if run.status == "running":
    run.cancel_requested_at = now
    return running_response
```

This is incomplete if the Worker process disappears before the next safe
boundary; the user-facing conversation can stay blocked forever.

#### Correct

```python
run.cancel_requested_at = now

# Worker boundary or Worker startup:
if run.status == "running" and run.cancel_requested_at is not None:
    mark_cancelled(run)
```

Cancellation is cooperative during the live call, but every cancelled run must
eventually converge to a PostgreSQL terminal state.
