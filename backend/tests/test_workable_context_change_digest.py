"""The Workable-context digest must change when (and only when) the
inputs feeding the pre-screen prompt change.

This is the trigger for the auto-rescore behavior in the sync loop:
when a starred role's existing application picks up a new questionnaire
answer, recruiter comment, or activity entry, the digest mismatch fires
a rescore so the LLM sees the new hard-constraint signal (e.g. a
recruiter comment saying "candidate is asking for 65k" against a
role with a 60k cap).
"""

from __future__ import annotations

from app.components.integrations.workable.sync_service import (
    _workable_context_digest,
)


def test_digest_stable_for_equivalent_inputs():
    a = _workable_context_digest(answers=[], comments=[], activities=[])
    b = _workable_context_digest(answers=None, comments=None, activities=None)
    assert a == b


def test_digest_changes_when_questionnaire_answer_added():
    before = _workable_context_digest(
        answers=[
            {"question_key": "visa", "body": "Yes"},
        ],
        comments=[],
        activities=[],
    )
    after = _workable_context_digest(
        answers=[
            {"question_key": "visa", "body": "Yes"},
            {"question_key": "salary", "body": "65,000 GBP"},
        ],
        comments=[],
        activities=[],
    )
    assert before != after


def test_digest_changes_when_recruiter_comment_added():
    before = _workable_context_digest(answers=[], comments=[], activities=[])
    after = _workable_context_digest(
        answers=[],
        comments=[{"body": "Candidate asking for 65k", "member": {"name": "Alex"}}],
        activities=[],
    )
    assert before != after


def test_digest_changes_when_activity_added():
    before = _workable_context_digest(answers=[], comments=[], activities=[])
    after = _workable_context_digest(
        answers=[],
        comments=[],
        activities=[
            {"action": "moved", "stage_name": "Applied", "to_stage": "Phone Screen"}
        ],
    )
    assert before != after


def test_digest_order_insensitive():
    """Workable sometimes reorders lists between fetches; that must not
    fire a spurious rescore."""
    order_a = _workable_context_digest(
        answers=[],
        comments=[
            {"body": "first", "member": {"name": "Alex"}},
            {"body": "second", "member": {"name": "Beth"}},
        ],
        activities=[],
    )
    order_b = _workable_context_digest(
        answers=[],
        comments=[
            {"body": "second", "member": {"name": "Beth"}},
            {"body": "first", "member": {"name": "Alex"}},
        ],
        activities=[],
    )
    assert order_a == order_b


def test_digest_handles_malformed_inputs_without_raising():
    """Workable shapes drift; the digest must not crash the sync."""
    # All non-list inputs collapse to "empty" and produce the empty digest.
    assert _workable_context_digest(
        answers="garbage",
        comments={"not": "a list"},
        activities=42,
    ) == _workable_context_digest(answers=[], comments=[], activities=[])
