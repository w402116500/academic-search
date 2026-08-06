# Academic Search Context

This glossary defines product terms for the research workspace, especially the
candidate-relevance review flow.

## Candidate Relevance

**Independent Relevance Assessment**:
An assessment of one candidate against the confirmed research context and that
candidate's allowed evidence. It is independent of the other candidates in a
batch and is not a relative ranking.
_Avoid_: Batch comparison, candidate ranking

**Relevance Evaluation Status**:
The trust status of a relevance assessment for one candidate. It is separate
from the decision whether that candidate may proceed to screening.
_Avoid_: Candidate error, screening status

**Screening Decision**:
The decision whether a candidate may proceed to subsequent review and admission.
It is made from a trustworthy relevance assessment and is not itself an
assessment status.
_Avoid_: Evaluation status, relevance result

**Completed Assessment (`completed`)**:
A relevance assessment whose conclusion and supporting evidence are trustworthy.
It may still conclude that a candidate is not recommended for screening.
_Avoid_: Relevant candidate, approved candidate

**Evidence-Bound Rationale**:
A user-facing relevance statement whose claim has directly linked, verbatim
evidence from the same candidate's title or abstract. A claim without its own
support is not user-facing.
_Avoid_: Free-form rationale, ungrounded explanation

**Background Candidate (`background`)**:
A trustworthy candidate that supports conceptual or contextual review rather
than core evidence. It remains user-visible and screening-eligible after Core
and Related candidates.
_Avoid_: Irrelevant candidate, core evidence

**Not Recommended (`not_recommended`)**:
A trustworthy negative relevance conclusion. The candidate is excluded from
user-facing candidate review without retry.
_Avoid_: Model failure, temporarily unavailable, unsupported assessment

**Information Insufficient (`insufficient_information`)**:
A candidate lacks the facts needed for a reliable relevance assessment, such as
an abstract. It is not a system failure and is excluded before model assessment
and user-facing candidate review, while remaining available to internal metrics.
_Avoid_: Model failure, user-review candidate, unsupported assessment

**Unsupported Assessment (`unsupported`)**:
An assessment whose claimed support cannot be established from the candidate's
allowed evidence. It is excluded from user-facing candidate review without a
replacement explanation, while retained as an internal prompt-quality signal.
_Avoid_: Temporary failure, user-review candidate, low relevance

**Temporarily Unavailable (`temporarily_unavailable`)**:
A candidate has no reliable assessment in the current execution because its
assessment could not be produced or validated. It is retried within a bounded
recovery policy, then excluded from user-facing candidate review while retained
for internal metrics and audit. It is distinct from an assessment that is
unsupported by the candidate evidence.
_Avoid_: Unsupported assessment, user-review candidate

## RAG Answer Citation

**Evidence Snapshot**:
The fixed set of retrieved evidence a model saw while producing one answer
attempt, including the mapping from model-facing evidence references to actual
document chunks.
_Avoid_: Conversation evidence, global citation list

**Evidence Ref**:
A short model-facing evidence label such as `E1` or `E2` that is meaningful only
inside one Evidence Snapshot. It is not a database identifier and must not be
shown as the final user citation number.
_Avoid_: Chunk ID, citation number, UUID

**Answer Attempt**:
One bounded attempt to answer a user's research question, including retrieval,
answer drafting, evidence verification, optional repair, and finalization or
failure.
_Avoid_: Conversation, assistant message

**User Citation Index**:
The user-facing citation number such as `[1]` or `[2]`, generated from the
final answer's first-use order. It is presentation-only and maps back through
the answer's Evidence Snapshot.
_Avoid_: Evidence Ref, chunk ID

**Claim Verifier**:
The independent checker that decides whether each factual claim in a draft
answer is supported by the provided evidence. It judges support; it does not
rewrite prose.
_Avoid_: Answer editor, citation renderer

**Final Composer**:
The answer editor that rewrites a draft after verification by using only
supported claims and explicit evidence-insufficient statements.
_Avoid_: Verifier, string deleter

**Citation Fragmentation Gate**:
The presentation-quality boundary for an otherwise supported answer whose
consecutive factual sentences repeatedly cite the same Evidence Ref.
_Avoid_: Claim Verifier, citation index, generic style judge

**Presentation Editor**:
A constrained editor that reorganizes verified answer claims into readable
user-facing prose without introducing new factual claims. It is separate from
the Final Composer used for unsupported claims.
_Avoid_: Final Composer, Claim Verifier, Answer Writer

**Presentation Edit Fallback**:
The successful publication of an original verified answer when the optional
Presentation Editor cannot produce another verified form.
_Avoid_: Research failure, clarification, editor retry
