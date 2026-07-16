"""Recruiter-facing copy contracts for autonomous-agent tool schemas."""

QUEUE_REASONING_DESC = (
    # Shown verbatim to the recruiter on the decision card, so the full
    # plain-English contract lives here. A tool description outranks the
    # system prompt, so "cite concrete fields" made the model write field
    # names literally.
    "1-3 short sentences a recruiter can read aloud. Lead with the "
    "recommendation and the one or two facts that justify it. Use plain "
    "words only: never internal identifiers or numeric IDs, never snake_case "
    'field or scorer keys (write "role fit", "pre-screen", "CV match"), and '
    'never key=value pairs (write "already at Technical Interview in '
    'Workable"). Keep it compact.'
)

QUEUE_EVIDENCE_DESC = (
    "Cited evidence: e.g. {cv_match_score: 87, taali_score: 78, "
    "criteria_hits: ['python', '5y SaaS'], cv_excerpt: '...'}."
)

__all__ = ["QUEUE_EVIDENCE_DESC", "QUEUE_REASONING_DESC"]
