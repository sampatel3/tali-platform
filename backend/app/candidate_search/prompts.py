"""Prompts for the natural-language search parser.

Single Haiku call: turn an NL query into a strict ``ParsedFilter`` JSON.
The system prompt enumerates the schema, lists region aliases, and gives
three worked examples covering the target queries.
"""

from __future__ import annotations

# Region → list of ISO-ish country names. Kept compact intentionally:
# the parser sees this verbatim, and a 200-country table inflates input
# tokens for marginal recall. Tune via real queries, not theory.
REGION_ALIASES: dict[str, list[str]] = {
    "europe": [
        "United Kingdom", "Ireland", "France", "Germany", "Spain", "Portugal",
        "Italy", "Netherlands", "Belgium", "Luxembourg", "Switzerland",
        "Austria", "Denmark", "Sweden", "Norway", "Finland", "Iceland",
        "Poland", "Czechia", "Slovakia", "Hungary", "Romania", "Bulgaria",
        "Greece", "Croatia", "Slovenia", "Estonia", "Latvia", "Lithuania",
        "Ukraine", "Serbia",
    ],
    "emea": [
        # Europe + Middle East & Africa anchors
        "United Kingdom", "Ireland", "France", "Germany", "Spain", "Italy",
        "Netherlands", "Switzerland", "Sweden", "Poland",
        "United Arab Emirates", "Saudi Arabia", "Qatar", "Egypt",
        "South Africa", "Nigeria", "Kenya", "Morocco", "Israel", "Turkey",
    ],
    "north america": ["United States", "Canada", "Mexico"],
    "apac": [
        "Australia", "New Zealand", "Singapore", "Japan", "South Korea",
        "Hong Kong", "Taiwan", "Vietnam", "Thailand", "Malaysia",
        "Indonesia", "Philippines", "India",
    ],
    "middle east": [
        "United Arab Emirates", "Saudi Arabia", "Qatar", "Bahrain", "Kuwait",
        "Oman", "Israel", "Jordan", "Lebanon", "Egypt", "Turkey",
    ],
}

# Country aliases the parser should normalise to canonical names.
# Server-side normalisation re-applies these defensively after parsing.
COUNTRY_ALIASES: dict[str, str] = {
    "uk": "United Kingdom",
    "u.k.": "United Kingdom",
    "great britain": "United Kingdom",
    "britain": "United Kingdom",
    "england": "United Kingdom",
    "scotland": "United Kingdom",
    "wales": "United Kingdom",
    "usa": "United States",
    "u.s.": "United States",
    "u.s.a.": "United States",
    "us": "United States",
    "america": "United States",
    "uae": "United Arab Emirates",
    "ksa": "Saudi Arabia",
}


SYSTEM_PROMPT = """You are a query parser for a recruiter's candidate-search box.

Your job: turn one natural-language query into ONE JSON object that matches the schema below. Extract structured filters where possible; keep qualitative phrases as soft_criteria; route untranslatable tokens to keywords.

Respond with ONLY the JSON object — no prose, no markdown, no code fences.

SCHEMA (every field optional, omit if empty):
{
  "skills_all":          [str],   // skills the candidate MUST have (AND)
  "skills_any":          [str],   // skills where ANY match is enough (OR)
  "locations_country":   [str],   // canonical country names (see normalisation)
  "locations_region":    [str],   // region keys: europe | emea | north america | apac | middle east
  "min_years_experience": int,    // null if unspecified
  "graph_predicates":    [{       // graph-shaped conditions (require Neo4j)
    "type": "worked_at" | "studied_at" | "colleague_of" | "n_hop_from",
    "value": str,                 // company / school name, or candidate identifier
    "n_hops": int                 // only for n_hop_from
  }],
  "soft_criteria":       [str],   // qualitative phrases that need rerank, e.g. "large enterprise", "in production"
  "keywords":            [str],   // residual tokens that didn't fit elsewhere; will run as ILIKE on cv_text
  "free_text":           str      // the original query, verbatim
}

NORMALISATION RULES:
- Country aliases: UK → "United Kingdom"; USA / US / America → "United States"; UAE → "United Arab Emirates"; KSA → "Saudi Arabia". Lower-case input matches case-insensitively.
- Regions: lowercase region keys. If user says "Europe" use locations_region: ["europe"], NOT a list of countries.
- Skills: keep technology names verbatim (case as given). Do not split multi-word skills ("AWS Glue", "Kubernetes Operators").
- Years: "5 years" / "5+ years" / "five years" → min_years_experience: 5. "senior" alone is NOT a years number — route to soft_criteria.
- Company-size phrases ("large enterprise", "Fortune 500", "FAANG", "startup", "scale-up") → soft_criteria.
- Industry phrases ("fintech", "healthcare", "logistics") → soft_criteria unless a specific employer is named.
- "Worked at <Company>" → graph_predicates: [{"type": "worked_at", "value": "<Company>"}]. Combine multiple "or" companies into multiple predicates.
- "in production" / "in prod" / "running production systems" → soft_criteria: ["in production"].
- If the query is gibberish or empty, return {"free_text": "<query>"} only.

EXAMPLES

Query: "candidates with AWS Glue experience"
{"skills_all":["AWS Glue"],"free_text":"candidates with AWS Glue experience"}

Query: "candidates who have worked in the UK"
{"locations_country":["United Kingdom"],"free_text":"candidates who have worked in the UK"}

Query: "candidates with 5 years experience, worked in Europe, large enterprise in production"
{"min_years_experience":5,"locations_region":["europe"],"soft_criteria":["large enterprise","in production"],"free_text":"candidates with 5 years experience, worked in Europe, large enterprise in production"}

Query: "Python and Kubernetes, worked at Google or Meta in last 3 years"
{"skills_all":["Python","Kubernetes"],"graph_predicates":[{"type":"worked_at","value":"Google"},{"type":"worked_at","value":"Meta"}],"keywords":["last 3 years"],"free_text":"Python and Kubernetes, worked at Google or Meta in last 3 years"}

Query: "senior engineers from FAANG based in London or Dublin"
{"locations_country":["United Kingdom","Ireland"],"soft_criteria":["senior","FAANG"],"keywords":["London","Dublin"],"free_text":"senior engineers from FAANG based in London or Dublin"}
"""


def build_parser_prompt(query: str) -> tuple[str, str]:
    """Return ``(system_prompt, user_prompt)`` for one parser call."""
    user_prompt = f"Query: {query.strip()}\n\nReturn the JSON object."
    return SYSTEM_PROMPT, user_prompt


def expand_region(region_key: str) -> list[str]:
    """Expand a region key to its country list, case-insensitive."""
    return list(REGION_ALIASES.get(region_key.strip().lower(), []))


def normalise_country(name: str) -> str:
    """Apply alias mapping; pass-through if not found."""
    if not name:
        return name
    cleaned = name.strip()
    return COUNTRY_ALIASES.get(cleaned.lower(), cleaned)
