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

**Cited Evidence**:
An evidence item that has a User Citation Index in the final answer and directly
supports visible answer prose. It is shown by default in the user-facing
"引用来源" list and must correspond to a citation in the answer body.
_Avoid_: Candidate evidence, all retrieved evidence, source-document metadata

**Cited Evidence List**:
The collapsed-by-default list of Cited Evidence beneath a completed answer. A
Citation Inspection opens it and focuses the matching evidence item.
_Avoid_: Permanently expanded source wall, hidden citations, candidate evidence
list

**Candidate Evidence**:
An item from the Evidence Snapshot that was retrieved or assessed but has no
User Citation Index in the final answer. It may be shown separately in an
advanced Deep Research view, but must not appear in the default
"引用来源" list.
_Avoid_: Cited evidence, answer source

**Safe Answer Markdown**:
The constrained presentation format for an assistant answer. It supports
headings, paragraphs, emphasis, lists, block quotes, and tables, while raw
HTML, embedded media, and executable content are not rendered. User Citation
Indices remain controlled UI elements rather than model-supplied HTML.
_Avoid_: Raw model HTML, rich-text execution, untrusted v-html

**Research Process Summary**:
The user-visible account of how one answer was produced. Fast RAG presents
only the current state and a compact completed audit summary. Deep Research may
add an expandable stage trace and separately exposed Candidate Evidence.
_Avoid_: Simulated token streaming, mandatory diagnostic logs, uniform detail
across modes

**Citation Audit Label**:
The compact user-visible statement of the verification boundary for one answer.
Fast RAG may state that its citations were checked; only Deep Research may state
that both citations and answer claims were verified. Both forms include the
actual cited-evidence count.
_Avoid_: Treating citation presence as claim verification, mode-agnostic trust
badge

**Citation Inspection**:
The in-place review action triggered by a User Citation Index. It expands the
Cited Evidence list, scrolls to the matching evidence item, and highlights it
without navigating away from the answer. Opening a document detail view is a
separate, deliberate action on the source title.
_Avoid_: Automatic route navigation, opaque citation tooltip, document-page
redirect

**Evidence-Insufficient Answer**:
A response state in which the current collection cannot support a factual
answer. It makes that boundary explicit, contains no Cited Evidence list, and
may ask a clarifying question or offer a Deep Research retry. It is not a
weaker form of a supported answer.
_Avoid_: Uncited conclusion, generic assistant error, hidden retrieval failure

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

## Workspace Lifecycle

**Permanent Workspace Deletion**:
An irreversible user action that removes a Research Workspace and its
workspace-owned research material to reclaim storage. It has no archive or
recovery period.
_Avoid_: Archive, hide, soft delete

**Deletion-Pending Workspace**:
A Research Workspace whose permanent deletion has started but whose private
resources have not all been removed. It remains visible but cannot accept
research work; the same deletion action may continue its cleanup. It is not
an active or archived workspace, and it is not yet deleted.
_Avoid_: Deleted workspace, temporarily archived workspace, active workspace

**Deletion Completion Barrier**:
The condition that every active workspace operation has been cancelled and
reached a terminal state before permanent workspace deletion completes.
_Avoid_: Force deletion, orphaned work

**Research Run Input Reference**:
An input message identifies the prompt that initiated a Research Run. Outside
of permanent workspace deletion, that reference and each evidence-to-chunk
reference protect the audit trail from accidental physical deletion. Permanent
workspace deletion explicitly removes the workspace's evidence and runs as one
private data set before removing the workspace root.
_Avoid_: Implicit cascade of ordinary audit data, cross-workspace run history

**Research Scope Document Detail**:
The metadata view for a document in the current research scope, available
without leaving evidence research. It includes bibliographic facts, citation,
source link, and ingestion state, but is not an in-app full-text reader.
_Avoid_: Candidate review, PDF reader, evidence-chunk viewer

**Irreversible Deletion Confirmation**:
The explicit second confirmation for permanent workspace deletion. It names
the workspace and warns that associated storage cannot be recovered, without
requiring manual name re-entry.
_Avoid_: Silent deletion, name re-entry
