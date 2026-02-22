"""Single source of truth for scoring categories/metrics and explanations."""

from __future__ import annotations

from typing import Dict, Any

SCORING_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "task_completion": {
        "label": "Task Completion",
        "description": "Measures delivery outcomes under assessment constraints: passing tests and finishing within time.",
        "metrics": ["tests_passed_ratio", "time_compliance", "time_efficiency"],
    },
    "prompt_clarity": {
        "label": "Prompt Clarity",
        "description": "Evaluates clarity, specificity, and actionability of prompts sent to the assistant.",
        "metrics": ["prompt_length_quality", "question_clarity", "prompt_specificity", "vagueness_score"],
    },
    "context_provision": {
        "label": "Context Provision",
        "description": "Checks whether prompts include useful context: code, errors, file/line references, and prior attempts.",
        "metrics": ["code_context_rate", "error_context_rate", "reference_rate", "attempt_mention_rate"],
    },
    "independence": {
        "label": "Independence & Efficiency",
        "description": "Assesses self-directed progress and efficient AI usage rather than over-reliance.",
        "metrics": ["first_prompt_delay", "prompt_spacing", "prompt_efficiency", "token_efficiency", "pre_prompt_effort"],
    },
    "utilization": {
        "label": "Response Utilization",
        "description": "Measures how effectively AI responses are turned into iterative code changes.",
        "metrics": ["post_prompt_changes", "wasted_prompts", "iteration_quality"],
    },
    "communication": {
        "label": "Communication Quality",
        "description": "Assesses writing quality and professional tone. Severe abusive language triggers hard caps.",
        "metrics": ["grammar_score", "readability_score", "tone_score"],
    },
    "approach": {
        "label": "Debugging & Design",
        "description": "Measures structured debugging behavior and system design/tradeoff thinking.",
        "metrics": ["debugging_score", "design_score"],
    },
    "cv_match": {
        "label": "CV-Job Fit",
        "description": "Estimates alignment between candidate background and role requirements (normalized from recruiter-fit 0-100 scoring).",
        "metrics": ["cv_job_match_score", "skills_match", "experience_relevance"],
    },
}

SCORING_METRICS: Dict[str, Dict[str, str]] = {
    "tests_passed_ratio": {"label": "Tests Passed", "description": "How many required tests passed out of the total suite."},
    "time_compliance": {"label": "Time Compliance", "description": "Whether the candidate completed within the allowed time limit."},
    "time_efficiency": {"label": "Time Efficiency", "description": "How efficiently the available time budget was used."},
    "prompt_length_quality": {"label": "Prompt Length", "description": "Whether prompts stay in an effective length range."},
    "question_clarity": {"label": "Clear Questions", "description": "How often prompts contain explicit, answerable questions."},
    "prompt_specificity": {"label": "Specificity", "description": "How targeted and concrete prompts are."},
    "vagueness_score": {"label": "Avoids Vagueness", "description": "Inverse signal of vague or ambiguous prompts."},
    "code_context_rate": {"label": "Includes Code", "description": "How often prompts include relevant code snippets."},
    "error_context_rate": {"label": "Includes Errors", "description": "How often prompts include actual errors/tracebacks."},
    "reference_rate": {"label": "References", "description": "How often prompts reference concrete files/lines or implementation points."},
    "attempt_mention_rate": {"label": "Prior Attempts", "description": "How often candidate states what was already tried."},
    "first_prompt_delay": {"label": "Thinks Before Asking", "description": "Whether candidate attempts independent reasoning before first prompt."},
    "prompt_spacing": {"label": "Prompt Spacing", "description": "Whether prompts are spaced with implementation effort between them."},
    "prompt_efficiency": {"label": "Prompts per Progress", "description": "Prompt efficiency relative to delivered test progress."},
    "token_efficiency": {"label": "Token Efficiency", "description": "Token budget efficiency relative to delivered progress."},
    "pre_prompt_effort": {"label": "Self-Attempt Rate", "description": "How often candidate changes code before prompting."},
    "post_prompt_changes": {"label": "Uses Responses", "description": "How often AI responses result in code changes."},
    "wasted_prompts": {"label": "Actionable Prompts", "description": "Fraction of prompts that moved implementation forward."},
    "iteration_quality": {"label": "Iterative Refinement", "description": "Whether follow-ups build on prior attempts."},
    "grammar_score": {"label": "Grammar", "description": "Basic writing quality and syntax correctness."},
    "readability_score": {"label": "Readability", "description": "How easy prompts are to read and parse."},
    "tone_score": {"label": "Professional Tone", "description": "Professionalism and absence of abusive language."},
    "debugging_score": {"label": "Debugging Strategy", "description": "Evidence of hypothesis-driven debugging and validation."},
    "design_score": {"label": "Design Thinking", "description": "Evidence of architecture and tradeoff reasoning."},
    "cv_job_match_score": {"label": "Overall Match", "description": "Overall CV/job fit estimate (internally normalized to /10 for assessment weighting)."},
    "skills_match": {"label": "Skills Alignment", "description": "Alignment between required skills and candidate profile (normalized to /10 for rubric weighting)."},
    "experience_relevance": {"label": "Experience Relevance", "description": "Relevance of prior experience to this role (normalized to /10 for rubric weighting)."},
}

SCORING_POLICIES: Dict[str, Any] = {
    "severe_language_communication_cap": 2.0,
    "severe_language_final_score_cap": 35.0,
    "fraud_final_score_cap": 50.0,
}


def scoring_metadata_payload() -> Dict[str, Any]:
    return {
        "categories": SCORING_CATEGORIES,
        "metrics": SCORING_METRICS,
        "policies": SCORING_POLICIES,
    }
