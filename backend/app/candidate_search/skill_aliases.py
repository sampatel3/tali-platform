"""Deterministic aliases for the most common recruiter search terms.

The source data uses LinkedIn-style taxonomy labels (for example
``Python (Programming Language)`` and ``Amazon Web Services (AWS)``), while a
recruiter naturally searches for ``python`` or ``aws``.  These aliases expand
the query without an embedding service or model call.  The SQL layer also does
case-insensitive substring matching, so the list only needs to cover genuine
synonyms and abbreviations rather than every stored label.
"""

from __future__ import annotations

import re


SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "aws": ("aws", "amazon web services"),
    "amazon web services": ("amazon web services", "aws"),
    "gcp": ("gcp", "google cloud platform", "google cloud"),
    "google cloud": ("google cloud", "google cloud platform", "gcp"),
    "k8s": ("k8s", "kubernetes"),
    "kubernetes": ("kubernetes", "k8s"),
    "js": ("js", "javascript"),
    "javascript": ("javascript", "js"),
    "ts": ("ts", "typescript"),
    "typescript": ("typescript", "ts"),
    "node": ("node", "node.js", "nodejs"),
    "nodejs": ("nodejs", "node.js", "node"),
    "postgres": ("postgres", "postgresql"),
    "postgresql": ("postgresql", "postgres"),
    "spark": ("spark", "apache spark"),
    "apache spark": ("apache spark", "spark"),
    "kafka": ("kafka", "apache kafka"),
    "apache kafka": ("apache kafka", "kafka"),
    "react": ("react", "react.js", "reactjs"),
    "reactjs": ("reactjs", "react.js", "react"),
    "dotnet": ("dotnet", ".net", "microsoft .net"),
    ".net": (".net", "dotnet", "microsoft .net"),
    "c sharp": ("c sharp", "c#"),
    "c#": ("c#", "c sharp"),
    "machine learning": ("machine learning", "ml"),
    "artificial intelligence": ("artificial intelligence", "ai"),
}


COMMON_SKILLS = frozenset(
    {
        "python", "sql", "java", "scala", "go", "golang", "rust", "ruby",
        "php", "terraform", "docker", "jenkins", "devops", "cybersecurity",
        "snowflake", "databricks", "hadoop", "pyspark", "airflow", "dbt",
        "etl", "power bi", "tableau", "azure", "aws glue", "salesforce",
        "sap", "oracle", "scrum", "agile", "project management",
        *SKILL_ALIASES.keys(),
    }
)


COMMON_TITLES = frozenset(
    {
        "project manager", "program manager", "programme manager",
        "scrum master", "product manager", "product owner", "data engineer",
        "software engineer", "backend engineer", "front end engineer",
        "frontend engineer", "full stack engineer", "cloud engineer",
        "devops engineer", "site reliability engineer", "solutions architect",
        "solution architect", "cloud architect", "data architect",
        "data scientist", "business analyst", "systems analyst",
        "delivery manager", "engineering manager", "technical lead",
        "tech lead", "security engineer", "network engineer",
    }
)


def normalize_term(value: str) -> str:
    """Lowercase and collapse punctuation/whitespace for alias lookup."""
    lowered = (value or "").strip().lower()
    lowered = re.sub(r"[_/]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered)


def expand_skill_term(value: str) -> list[str]:
    """Return stable, deduplicated query variants for one skill."""
    original = normalize_term(value)
    variants = SKILL_ALIASES.get(original, (original,))
    out: list[str] = []
    for item in variants:
        normalized = normalize_term(item)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def is_common_skill(value: str) -> bool:
    return normalize_term(value) in COMMON_SKILLS


def is_common_title(value: str) -> bool:
    return normalize_term(value) in COMMON_TITLES
