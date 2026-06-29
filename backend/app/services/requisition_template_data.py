"""Built-in requisition spec template DATA — the constants the org starts with.

Pure data, no logic: the default JD (job-spec) document and the default
requisition spec template. Split out of ``requisition_template_service`` so the
service module (resolve/validate/coerce logic) stays small; the service
re-exports these names, so ``requisition_template_service.DEFAULT_REQUISITION_TEMPLATE``
keeps working for every existing caller and test.

  * ``DEFAULT_JD_TEMPLATE`` — the default job-spec markdown the live panel renders
    (``{{placeholders}}`` the frontend fills from the captured brief).
  * ``DEFAULT_REQUISITION_TEMPLATE`` — the built-in spec template (sections in
    display order; ``question`` is the prompt the agent asks; ``required`` drives
    completeness + the gap engine; ``options`` is present only on ``select``).
"""
from __future__ import annotations

from typing import Any

# The default JOB-SPEC (JD) document the live panel renders. Markdown with
# {{placeholders}} the frontend fills from the captured brief. The prose
# sections (About us / Benefits / EEO) are boilerplate an org sets once; the
# placeholders are populated on the fly as the conversation captures the spec.
# Supported placeholders: title, domain, summary, department, seniority, location,
# workplace_type, employment_type, openings, salary, urgency, responsibilities,
# must_haves, preferred, dealbreakers, success_profile, assessment_focus, evp.
DEFAULT_JD_TEMPLATE = """# {{title}}

{{summary}}

**Domain:** {{domain}}
**Details:** {{location}} · {{workplace_type}} · {{employment_type}} · {{seniority}}
**Openings:** {{openings}} · **Compensation:** {{salary}} · **Urgency:** {{urgency}}

## About the role
{{summary}}

## What you'll do
{{responsibilities}}

## What we're looking for
{{must_haves}}

**Nice to have**
{{preferred}}

## What success looks like
{{success_profile}}

## Why join us
{{evp}}

## About us
{{company_description}}

## Benefits
{{benefits}}

---
_We're committed to an inclusive, accessible hiring process. Add your EEO / reasonable-adjustments statement here._
"""


# The built-in template. Mirrors the fixed contract the frontend is built
# against — sections in display order; ``question`` is the natural prompt the
# agent asks; ``required`` drives completeness + the gap engine; ``options``
# is present only on ``select`` fields.
DEFAULT_REQUISITION_TEMPLATE: dict[str, Any] = {
    "version": 1,
    "jd_template": DEFAULT_JD_TEMPLATE,
    "sections": [
        {
            "key": "role_basics",
            "label": "Role basics",
            "fields": [
                {
                    "key": "title",
                    "label": "Title",
                    "type": "text",
                    "required": True,
                    "question": "What role are you hiring for?",
                },
                {
                    "key": "domain",
                    "label": "Domain / industry",
                    "type": "text",
                    "required": True,
                    "question": "What domain or industry is this role in? (e.g. banking, healthcare, e-commerce) — it changes what 'good' looks like.",
                },
                {
                    "key": "department",
                    "label": "Department",
                    "type": "text",
                    "required": False,
                    "question": "Which team or department is this in?",
                },
                {
                    "key": "seniority",
                    "label": "Seniority",
                    "type": "select",
                    "required": True,
                    "question": "What seniority level?",
                    "options": [
                        "Intern",
                        "Junior",
                        "Mid",
                        "Senior",
                        "Staff",
                        "Lead",
                        "Principal",
                        "Director",
                        "VP",
                    ],
                },
                {
                    "key": "summary",
                    "label": "One-line summary",
                    "type": "longtext",
                    "required": True,
                    "question": "In one line, what will this person do?",
                },
            ],
        },
        {
            "key": "logistics",
            "label": "Logistics",
            "fields": [
                {
                    "key": "location_city",
                    "label": "City",
                    "type": "text",
                    "required": False,
                    "question": "Which city is this based in?",
                },
                {
                    "key": "location_country",
                    "label": "Country",
                    "type": "text",
                    "required": False,
                    "question": "Which country?",
                },
                {
                    "key": "workplace_type",
                    "label": "Workplace type",
                    "type": "select",
                    "required": True,
                    "question": "Is this onsite, hybrid, or remote?",
                    "options": ["Onsite", "Hybrid", "Remote"],
                },
                {
                    "key": "employment_type",
                    "label": "Employment type",
                    "type": "select",
                    "required": True,
                    "question": "Full-time, part-time, contract, or temporary?",
                    "options": ["Full-time", "Part-time", "Contract", "Temporary"],
                },
                {
                    "key": "openings",
                    "label": "Openings",
                    "type": "number",
                    "required": True,
                    "question": "How many are you hiring?",
                },
                {
                    "key": "urgency",
                    "label": "Hiring urgency",
                    "type": "select",
                    "required": True,
                    "question": "How urgent is this hire?",
                    "options": ["Low", "Normal", "High", "Urgent"],
                },
                {
                    "key": "target_start_date",
                    "label": "Target start date",
                    "type": "date",
                    "required": False,
                    "question": "When do you want them to start?",
                },
            ],
        },
        {
            "key": "compensation",
            "label": "Compensation",
            "fields": [
                # Compensation is HR/People's call — the intake agent NEVER asks
                # for it (see comp_instruction in the prompt). Left here, optional,
                # so a recruiter CAN record it manually + the JD has a slot, but it
                # is not a gap the agent chases and it never blocks completeness.
                {
                    "key": "salary_min",
                    "label": "Salary (min)",
                    "type": "number",
                    "required": False,
                    "question": "What's the bottom of the salary range?",
                },
                {
                    "key": "salary_max",
                    "label": "Salary (max)",
                    "type": "number",
                    "required": False,
                    "question": "And the top of the range?",
                },
                {
                    "key": "salary_currency",
                    "label": "Currency",
                    "type": "select",
                    "required": False,
                    "question": "Which currency?",
                    "options": ["AED", "USD", "GBP", "EUR", "SAR", "INR"],
                },
                {
                    "key": "salary_period",
                    "label": "Pay period",
                    "type": "select",
                    "required": False,
                    "question": "Per year, month, day, or hour?",
                    "options": ["year", "month", "day", "hour"],
                },
                {
                    "key": "bonus",
                    "label": "Bonus",
                    "type": "text",
                    "required": False,
                    "question": "Any bonus?",
                },
                {
                    "key": "equity",
                    "label": "Equity",
                    "type": "text",
                    "required": False,
                    "question": "Any equity?",
                },
                {
                    "key": "benefits",
                    "label": "Benefits",
                    "type": "list",
                    "required": False,
                    "question": "What benefits come with this role?",
                },
            ],
        },
        {
            "key": "requirements",
            "label": "Requirements",
            "fields": [
                {
                    "key": "must_haves",
                    "label": "Must-haves",
                    "type": "list",
                    "required": True,
                    "question": "What are the non-negotiables?",
                },
                {
                    "key": "preferred",
                    "label": "Nice-to-haves",
                    "type": "list",
                    "required": False,
                    "question": "What's nice to have but not essential?",
                },
                {
                    "key": "dealbreakers",
                    "label": "Dealbreakers",
                    "type": "list",
                    "required": False,
                    "question": "Any automatic no?",
                },
            ],
        },
        {
            "key": "context",
            "label": "Hiring context",
            "fields": [
                {
                    "key": "success_profile",
                    "label": "Success profile",
                    "type": "longtext",
                    "required": True,
                    "question": "What does great look like in 6 months?",
                },
                {
                    "key": "responsibilities",
                    "label": "Key responsibilities",
                    "type": "list",
                    "required": True,
                    "question": "What are the key responsibilities / duties?",
                },
                {
                    "key": "priorities",
                    "label": "Weighted priorities",
                    "type": "struct_list",
                    "required": False,
                    "question": "What matters most — and how would you weight it?",
                },
                {
                    "key": "tradeoffs",
                    "label": "Trade-offs",
                    "type": "list",
                    "required": False,
                    "question": "What would you trade off?",
                },
                {
                    "key": "calibration_exemplars",
                    "label": "Calibration examples",
                    "type": "struct_list",
                    "required": False,
                    "question": "Anyone (strong or weak) that calibrates the bar?",
                },
                {
                    "key": "sourcing_signals",
                    "label": "Sourcing signals",
                    "type": "list",
                    "required": False,
                    "question": "Where do great candidates come from?",
                },
                {
                    "key": "assessment_focus",
                    "label": "Assessment focus",
                    "type": "list",
                    "required": False,
                    "question": "What should we test for?",
                },
                {
                    "key": "process",
                    "label": "Interview process",
                    "type": "longtext",
                    "required": False,
                    "question": "What's the interview process?",
                },
                {
                    "key": "evp",
                    "label": "Why this job",
                    "type": "list",
                    "required": False,
                    "question": "Why would someone want this job?",
                },
            ],
        },
    ],
}
