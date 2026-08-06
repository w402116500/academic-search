# RAG Answer Citation And Verification

## Scenario: EvidenceSnapshot-Based RAG Answer Verification

### 1. Scope / Trigger

Use this contract when changing research-chat RAG answer generation, evidence
verification, answer repair, answer persistence, citation APIs, or frontend
reference rendering. The core invariant is that the database `chunk_id`, the
model-facing evidence reference, and the user-facing citation number are three
different identities with different audiences.

This is a cross-layer contract because the flow spans retrieval, prompt
construction, structured model output, verifier output, persisted answer
attempt diagnostics, API response references, and frontend display.

### 2. Signatures

#### Model-facing identifiers

```python
EvidenceRef = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]*$")]
ClaimId = Annotated[str, StringConstraints(pattern=r"^C[1-9][0-9]*$")]
```

`EvidenceRef` is scoped to one `EvidenceSnapshot`. It is never globally unique.

#### Answer draft target contract

```python
class AnswerClaimDraft(BaseModel):
    claim_id: ClaimId
    text: str
    refs: list[EvidenceRef]

class AnswerDraft(BaseModel):
    answer: str
    cited_refs: list[EvidenceRef]
    claims: list[AnswerClaimDraft]
    evidence_sufficient: bool
    clarification_question: str | None = None
```

The current UUID-based `cited_chunk_ids` contract must be treated as legacy for
this flow. New prompts and model schemas must use `cited_refs`.

#### Claim verifier target contract

```python
class AnswerClaimVerificationItem(BaseModel):
    claim_id: ClaimId
    claim: str
    supported: bool
    supporting_refs: list[EvidenceRef]

class AnswerClaimVerification(BaseModel):
    claims: list[AnswerClaimVerificationItem]
```

The verifier must receive and return `EvidenceRef` values only. UUID-valued
`supporting_chunk_ids` are legacy and must not appear in model prompts.

#### Final composer target contract

```python
class FinalAnswerDraft(BaseModel):
    answer: str
    cited_refs: list[EvidenceRef]
    resolved_claim_ids: list[ClaimId]
    evidence_insufficient_claims: list[ClaimId]
```

The final composer is used only after at least one draft claim is unsupported.
It rewrites the answer; it must not directly splice the original string.

#### Persistence records

If `research_runs` remains the physical attempt table, it must expose the same
semantics as `AnswerAttempt`.

```text
AnswerAttempt
  id
  conversation_id
  user_message_id or research_run_id
  status = drafting | verifying | repairing | completed | failed_protocol | failed_model | failed_budget
  snapshot_id
  failure_code
  failure_detail
  created_at
  completed_at

EvidenceSnapshot
  id
  answer_attempt_id
  conversation_id
  created_at

EvidenceSnapshotItem
  snapshot_id
  evidence_ref
  chunk_id
  rank
  source_title
  page_start
  page_end
  snippet

unique(snapshot_id, evidence_ref)
```

Successful assistant messages reference the completed `AnswerAttempt` /
`EvidenceSnapshot`. Failed attempts are retained for diagnostics and retry, but
are not displayed as ordinary assistant messages.

#### API reference payload

```json
{
  "answer": "...[1]...[2]",
  "references": [
    {
      "display_index": 1,
      "evidence_ref": "E3",
      "chunk_id": "uuid",
      "source_title": "paper title",
      "page_start": 3,
      "page_end": 4,
      "snippet": "..."
    }
  ]
}
```

`display_index` is generated from the final answer's first-use order. It is not
copied from `EvidenceRef`.

### 3. Contracts

Retrieval returns real `chunk_id` values. Before any answer or verifier prompt is
built, the server creates an `EvidenceSnapshot` and assigns sequential
`EvidenceRef` values in the evidence order passed to the model:

```text
S1:
  E1 -> chunk_uuid_a
  E2 -> chunk_uuid_b
  E3 -> chunk_uuid_c
```

Prompts may show:

```text
[E1]
Paper: ...
Location: ...
Text: ...
```

Prompts must not show:

```text
chunk_id=...
```

The answer model must cite only `EvidenceRef` values from the current snapshot.
The server validates `cited_refs` and each draft claim's `refs` as a subset of
the snapshot refs before verifier execution.
The answer prose citations must describe the same ref set as structured
`cited_refs`; a draft that says `【E2】` in prose but returns
`cited_refs=["E1"]` is a protocol error.

The claim verifier must judge factual support only from the answer's actual
cited refs. To preserve snapshot identity, the backend may render only the
cited subset into the verifier prompt, but it must keep the original
EvidenceSnapshot refs instead of renumbering:

```text
Snapshot: E1 -> chunk_a, E2 -> chunk_b
Answer cites: E2
Verifier prompt: [E2] chunk_b text
```

It must not render `chunk_b` as `[E1]` merely because it is the first verifier
input. This prevents the verifier from confusing answer citations with a
renumbered prompt-local evidence list.

For a supported claim, `supporting_refs` must be non-empty and a subset of the
answer's cited refs. For an unsupported claim, `supporting_refs` must be empty.
`supported=false` with `supporting_refs=[]` is a normal semantic result, not a
citation mapping error.

If every verifier item is supported, the draft can go directly to finalization
after citation-number rendering. If any item is unsupported, the flow enters the
final composer and then runs a second verifier pass before finalization.

The final composer may use only:

- verifier-supported claims;
- explicit "current evidence is insufficient to prove X" statements for user
  questions whose core claim was unsupported.

The composer must not introduce new factual claims. The post-composer verifier
pass catches any accidental new or reintroduced unsupported claim.

User-facing citation numbers are rendered from the final answer's first
appearance order:

```text
Final E-ref order: E3, E1, E3, E2
User citations:    E3 -> [1], E1 -> [2], E2 -> [3]
```

Unused snapshot items are not returned to the frontend and do not occupy
display numbers.

### 4. Validation And Error Matrix

| Condition | Classification | Action |
| --- | --- | --- |
| Answer output contains `E9` absent from the snapshot | Protocol error | Mark the attempt `failed_protocol`; persist snapshot and raw-output summary; do not create an assistant message |
| Answer or verifier output contains a UUID where an `EvidenceRef` is required | Protocol error | Mark the attempt `failed_protocol`; treat as model/protocol leakage |
| `evidence_sufficient=true` with empty `cited_refs` | Schema/contract error | Reject the draft before verifier execution |
| Answer prose refs and structured `cited_refs` differ | Protocol error | Reject before verifier execution; do not let verifier judge a different evidence set from the one the answer shows |
| Draft claim refs are not a subset of `cited_refs` and snapshot refs | Protocol error | Reject the draft; persist diagnostics |
| Verifier item has `supported=true` with empty `supporting_refs` | Schema/contract error | Reject verifier output |
| Verifier item has `supported=false` with non-empty `supporting_refs` | Schema/contract error | Reject verifier output |
| Verifier `supporting_refs` contains a snapshot ref not cited by the answer | Protocol error | Reject verifier output; the verifier cannot use uncited evidence to rescue an answer claim |
| Verifier returns `supported=false` with `supporting_refs=[]` | Semantic unsupported | Enter composer repair path; do not treat as an ID failure |
| Composer output contains an unknown ref or UUID | Protocol error | Mark the repair attempt failed; persist diagnostics |
| Composer path's second verifier still finds unsupported factual claims | Evidence insufficient | Return a bounded insufficiency answer or clarification; do not silently publish unsupported claims |
| Final answer first uses `E3` then `E1` | Display remapping | Render `E3 -> [1]`, `E1 -> [2]`; never show `[3]` then `[1]` |

### 5. Good / Base / Bad Cases

- Good: retrieval returns `E1`, `E2`, `E3`; the answer first cites `E3` then
  `E1`; all cited refs map back through the snapshot; the API returns display
  references `[1] = E3 -> chunk_uuid_c` and `[2] = E1 -> chunk_uuid_a`.
- Base: all draft claims are supported by their cited refs; skip the final
  composer and render citations directly.
- Base: one cost-reduction claim is unsupported; the composer rewrites the
  answer to state that current evidence does not prove cost reduction, then a
  second verifier pass succeeds.
- Bad: asking the verifier to output `supporting_chunk_ids` UUIDs and rejecting
  the run because it produced a syntactically valid UUID that was not in the
  current evidence set.
- Bad: deleting unsupported sentences with string replacement and publishing
  the remaining prose without re-composition.
- Bad: showing user citations as `[3] [7]` because the final answer cited `E3`
  and `E7`.

### 6. Tests Required

- Prompt tests must assert that RAG answer and verifier prompts contain
  `[E1]`-style refs and do not contain `chunk_id=` or UUID text.
- Schema tests must reject UUIDs, unknown refs, `supported=true` without
  `supporting_refs`, and `supported=false` with non-empty `supporting_refs`.
- Snapshot tests must assert `unique(snapshot_id, evidence_ref)`, allow `E1` to
  repeat across snapshots, and map refs back to the correct chunk UUID.
- Single-RAG tests must cover:
  - all-supported direct finalization;
  - unsupported draft claim entering composer repair;
  - composer output receiving a second verifier pass;
  - protocol failure persisting a failed attempt without an assistant message.
- Prompt/protocol tests must cover verifier cited-subset rendering without
  EvidenceRef renumbering, answer-prose/`cited_refs` mismatch rejection, and
  verifier rejection when `supporting_refs` uses an uncited snapshot ref.
- API tests must assert that final references include `display_index`,
  `evidence_ref`, and `chunk_id`, and that display indexes follow first-use
  order without gaps.
- Frontend tests must assert that ordinary answer UI renders `[n]` citations and
  never displays `E1` / `E2` or UUIDs as citation labels.
- Regression tests must cover the previous failure mode where a verifier returns
  a valid-looking UUID outside the allowed evidence set.

### 7. Wrong Vs Correct

#### Wrong

```text
[E1] chunk_id=7c8a...
Text: ...

The answer schema requires cited_chunk_ids: [UUID].
The verifier schema requires supporting_chunk_ids: [UUID].
```

This exposes internal identity to the model and makes a hallucinated UUID look
like a plausible structured answer.

#### Correct

```text
[E1]
Text: ...

Answer schema: cited_refs = ["E1"]
Verifier schema: supporting_refs = ["E1"]
Server mapping: snapshot_id + "E1" -> chunk UUID
```

The model handles only short snapshot-local refs; the server remains the only
owner of UUID resolution.

#### Wrong

```python
final_answer = draft_answer.replace(unsupported_sentence, "")
```

This can break grammar, leave dangling transitions, or preserve derived claims
that depended on the removed sentence.

#### Correct

```text
Draft -> verifier marks unsupported claims -> final composer rewrites using
only supported claims and explicit evidence-insufficient statements -> verifier
checks the repaired answer.
```

The verifier is the judge. The final composer is the editor.

#### Wrong

```text
Answer text: cites 【E2】
Verifier prompt: [E1] same chunk text because the cited subset was renumbered
Verifier output: supporting_refs=["E1"]
```

This makes the same chunk carry two model-facing identities inside one answer
attempt.

#### Correct

```text
Snapshot: E1 -> chunk_a, E2 -> chunk_b
Answer text: cites 【E2】
Verifier prompt: [E2] chunk_b text
Verifier output: supporting_refs=["E2"]
```

EvidenceRef belongs to the answer attempt's snapshot, not to each prompt call.

## Scenario: Conditional Presentation Editing After Successful Verification

### 1. Scope / Trigger

Use this contract when a supported RAG answer reads like a list of mechanically
translated facts, especially when consecutive factual sentences repeat one
`EvidenceRef`. This is a presentation-quality branch, not an evidence-quality
branch: it may run only after the original Answer Claim Verification has no
unsupported claims.

### 2. Signatures

```python
class CitationFragmentationAssessment(BaseModel):
    triggered: bool
    citation_bearing_sentence_count: int
    max_same_ref_sentence_run: int
    repeated_ref: EvidenceRef | None = None

class PresentationAnswerDraft(BaseModel):
    answer: str
    cited_refs: list[EvidenceRef]
```

The Presentation Editor input is the user question plus the verifier items with
`supported=true` and their `supporting_refs`. It must not receive full evidence
blocks, raw chunk UUIDs, or the original Writer draft as model input.

### 3. Contracts

The Writer prompt for a short factual question must use natural Chinese: answer
the question first, include only necessary supporting details, and put a shared
source reference at the end of one coherent semantic span. It must not require
the same `EvidenceRef` after every short sentence.

After original verification succeeds, split the draft only on `。！？`. A
citation-bearing sentence has at least one recognized `【E1】` or `[E1]` token.
Trigger `CitationFragmentationAssessment.triggered` only if at least three
adjacent citation-bearing sentences each contain the exact same single
`EvidenceRef`. Do not infer whether prose is natural, infer question facets,
or split decimal values such as `p<0.001`.

When triggered, make exactly one Presentation Editor attempt. Its editor call
and post-edit Claim Verifier share an aggregate 45-second deadline and both
consume the run's normal model-call budget. The editor may reorganize and
paraphrase supported claims but may not add factual content, use another ref,
or turn a correlation into causation. Validate its prose refs before the second
verifier; only a fully supported edited answer replaces the original draft.

Persist server-only presentation diagnostics at:

```json
{
  "presentation_quality": {
    "citation_fragmentation": {
      "triggered": true,
      "citation_bearing_sentence_count": 5,
      "max_same_ref_sentence_run": 5,
      "repeated_ref": "E1"
    },
    "writer_answer": "...【E1】...",
    "presentation_edit": {
      "status": "skipped | applied | fallback",
      "editor_answer": "...【E1】...",
      "fallback_reason": "timeout | model_error | protocol_error | verifier_rejected"
    }
  }
}
```

This data belongs under `research_runs.retrieval_trace`; it must not be added to
the ordinary conversation-message or research-run API DTOs.

### 4. Validation And Error Matrix

| Condition | Classification | Action |
| --- | --- | --- |
| Original verifier finds an unsupported claim | Evidence correctness path | Use Final Composer; do not invoke Presentation Editor |
| Gate does not trigger | Normal supported answer | Render original verified draft directly |
| Editor returns unknown ref, UUID, or no prose ref | Presentation protocol failure | Record fallback and publish original verified draft |
| Editor or second verifier exceeds 45-second aggregate budget | Presentation timeout | Record fallback and publish original verified draft |
| Second verifier finds an unsupported editor claim | Presentation verification failure | Record fallback and publish original verified draft |
| Editor succeeds and second verifier supports all claims | Presentation success | Render edited answer and user citation numbers |

### 5. Good / Base / Bad Cases

- Good: five adjacent `【E1】` factual sentences are grouped into a natural answer
  with one paragraph-end `【E1】`, then the second verifier supports it.
- Base: a short answer already uses one coherent `【E1】` span; the gate skips the
  editor and adds no model calls.
- Base: the editor times out; the user receives the original verified answer and
  the trace records `presentation_edit.fallback_reason="timeout"`.
- Bad: treating any single-source answer as defective, even when it is one clear
  cited sentence.
- Bad: passing full evidence blocks to the editor so it can add valid-but-unasked
  facts not present in the verifier-approved claim set.

### 6. Tests Required

- Unit-test sentence segmentation and the three-adjacent-single-ref threshold;
  assert `p<0.001` remains within one sentence.
- Unit-test that grouped citations, fewer than three adjacent cited sentences,
  and varied refs do not trigger the editor.
- Unit-test the Presentation Editor prompt/input contains only question,
  supported claim text, and `EvidenceRef` values; it must not contain evidence
  content, `chunk_id`, UUID text, or original draft text.
- Graph-test successful edit plus a second verifier, and verify it consumes two
  additional model calls only when the Gate triggers.
- Graph-test timeout, model/protocol error, and rejected second verifier all
  preserve the original verified answer and write the fallback trace.
- Repository/API-test that `presentation_quality` persists in `retrieval_trace`
  but raw Writer/Editor answers do not appear in ordinary response DTOs.

### 7. Wrong Vs Correct

#### Wrong

```text
Every supported answer -> Presentation Editor -> second verifier
Editor failure -> clarification or failed run
```

This adds latency to normal answers and turns a cosmetic failure into loss of a
verified answer.

#### Correct

```text
Original verifier passes
  -> deterministic Citation Fragmentation Gate
  -> skip directly, or one bounded Presentation Editor + second verifier
  -> editor failure falls back to the original verified answer
```

The Claim Verifier remains the factual judge. The Presentation Editor is an
optional, bounded re-expression step.
