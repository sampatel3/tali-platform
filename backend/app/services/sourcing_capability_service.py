"""Machine-readable boundaries for external sourcing integrations.

LinkedIn RSC can receive a recruiter-initiated One-Click Export, while RSC+
can connect an ATS project to LinkedIn Hiring Assistant. Neither product
exposes a Taali-controlled people-search API. Keep that distinction in one
place so the role agent never mistakes a delegated LinkedIn agent or a human
export event for a sourcing tool it can invoke itself.
"""

from __future__ import annotations

from typing import Any


def linkedin_sourcing_capability() -> dict[str, Any]:
    return {
        "provider": "linkedin_rsc",
        "status": "partner_access_required",
        "capability": "one_click_export",
        "autonomous_search": False,
        "human_export_required": True,
        "manual_copy_paste_required": False,
        "delegated_agent_option": {
            "provider": "linkedin_hiring_assistant",
            "integration": "rsc_plus_connected_projects",
            "status": "commercial_and_partner_access_required",
            "orchestration_owner": "linkedin",
            "taali_controlled_search_api": False,
        },
    }
