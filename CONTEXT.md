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
