"""Post-capture reply grounding for requisition chat."""

from __future__ import annotations

import re
from typing import Any, Optional

from ..models.role_brief import RoleBrief
from .requisition_chat_capture import compute_gaps, next_gap_prompt
from .requisition_template_service import iter_fields

_ACTION_REQUEST_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:please|can\s+you|could\s+you|would\s+you|yes[,.]?)\s+(?:publish|post|launch|activate)\b",
        r"\b(?:publish|post|launch|activate)\s+(?:it|this|now|job|role|opening|requisition|the\s+(?:job|role|opening|requisition))\b",
        r"\bcreate\s+(?:the|this|a|an|your)?\s*(?:job|role|opening|requisition)\b",
        r"\b(?:start|begin)\s+sourcing\b",
        r"\bturn\s+(?:the\s+)?agent\s+on\b",
        r"\b(?:go|make\s+(?:it|this|the\s+(?:job|role|opening)))\s+live\b",
        r"\block\s+(?:it|this|the\s+)?(?:job\s+)?spec(?:ification)?\b",
    )
)
_ACTION_CLAIM_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:i|we)(?:'ve|\s+have|\s+just)?\s+(?:published|posted|launched|activated|created|opened|locked)\b",
        r"\b(?:job|role|opening|requisition|spec(?:ification)?|it)\s+(?:is|are|was|has\s+been|is\s+now|are\s+now)\s+(?:live|active|published|posted|launched|created|open|locked)\b",
        r"\b(?:published|posted|launched|activated|created|opened)\s+(?:the|this|your)\s+(?:job|role|opening|requisition)\b",
        r"\bsourcing\s+(?:(?:has|is)\s+)?(?:started|underway|active)\b",
        r"\b(?:i|we)(?:'ve|\s+have|\s+just)?\s+(?:started\s+sourcing|turned\s+(?:the\s+)?agent\s+on)\b",
        r"\b(?:done|perfect|success)[^\n.!?]{0,80}\b(?:live|active|published|posted|launched|created)\b",
    )
)


def _chat_action_label(brief: RoleBrief, client_org_name: Optional[str]) -> str:
    if client_org_name:
        return "Submit brief"
    if getattr(brief, "source_role_id", None):
        return "Create and score candidates"
    return "Publish job page"


def ground_assistant_reply(
    *,
    brief: RoleBrief,
    template: dict[str, Any],
    message: str,
    model_reply: str,
    document_turn: bool,
    attachment_error: bool,
    attachment_warning: bool,
    source_updated: bool,
    change_mode: str,
    changed_keys: list[str],
    client_org_name: Optional[str],
) -> tuple[str, bool]:
    """Return a reply grounded in post-capture state and endpoint capability."""

    if attachment_error:
        return (
            "I couldn't extract usable content from the attached file, so I "
            "haven't populated the brief from it. Please re-export it as PDF, "
            "DOCX, or text, or attach a clear JPG/PNG image.",
            True,
        )

    if change_mode == "clarify":
        relation = (
            " The original ATS role and its candidates will remain unchanged."
            if getattr(brief, "source_role_id", None)
            else ""
        )
        return (
            "The attached document materially differs from the current draft. "
            "Should I replace the current draft with it, or apply only its "
            f"differences?{relation}",
            False,
        )

    reply = str(model_reply or "").strip()
    message_for_detection = str(message or "").replace("’", "'")
    reply_for_detection = reply.replace("’", "'")
    action_requested = any(
        pattern.search(message_for_detection) for pattern in _ACTION_REQUEST_PATTERNS
    )
    action_claimed = any(
        pattern.search(reply_for_detection) for pattern in _ACTION_CLAIM_PATTERNS
    )
    should_override = (
        document_turn
        or bool(changed_keys)
        or action_requested
        or action_claimed
        or not reply
    )
    if not should_override:
        return reply, False

    gaps = compute_gaps(brief, template)
    next_question, _options = next_gap_prompt(template, brief)
    labels_by_key = {
        field["key"]: str(field.get("label") or field["key"])
        for _section, field in iter_fields(template)
    }
    changed_labels = [labels_by_key.get(key, key) for key in changed_keys]
    if change_mode == "replace" and source_updated:
        if getattr(brief, "source_role_id", None):
            lead = (
                "I've replaced the role content in this related-role draft. "
                "The original ATS role and shared candidate pool are unchanged."
            )
        else:
            lead = "I've made the new specification canonical for this draft."
    elif changed_keys and getattr(brief, "source_role_id", None):
        lead = (
            "I've amended this related-role draft; the original ATS role and "
            "shared candidate pool are unchanged."
        )
    elif changed_keys:
        lead = "I've amended the draft."
    elif document_turn and source_updated:
        lead = "I've populated the brief from the available source material."
    elif document_turn:
        lead = (
            "I've checked the available source material, but it didn't add any "
            "new brief fields."
        )
    else:
        lead = "I've saved the information you provided."
    if changed_labels:
        lead += " Updated: " + ", ".join(changed_labels[:6]) + "."
    if attachment_warning:
        lead += (
            " I couldn't read every attachment; please re-export or resend any "
            "file whose content is missing."
        )
    if gaps:
        remaining = ", ".join(gap["label"] for gap in gaps[:3])
        boundary = (
            " I haven't performed the requested action; this chat only saves "
            "brief fields."
            if action_requested or action_claimed
            else ""
        )
        return f"{lead}{boundary} I still need {remaining}. {next_question}", True

    action_label = _chat_action_label(brief, client_org_name)
    return (
        f"{lead} All required fields are captured and the brief is ready for "
        f"review. I haven't performed the final action; use the '{action_label}' "
        "button when you're ready.",
        True,
    )


__all__ = ["ground_assistant_reply"]
