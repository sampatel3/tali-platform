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

_CANONICAL_COUNTRY_BY_LOWER = {
    country.lower(): country
    for countries in REGION_ALIASES.values()
    for country in countries
}
_CANONICAL_COUNTRY_BY_LOWER.update(
    {country.lower(): country for country in COUNTRY_ALIASES.values()}
)
CANONICAL_COUNTRIES = frozenset(_CANONICAL_COUNTRY_BY_LOWER.values())


SYSTEM_PROMPT = """You are a query parser for a recruiter's candidate-search box.

Your job: turn one natural-language query into ONE JSON object that matches the schema below. Extract structured filters where possible; keep REQUIRED qualitative phrases as soft_criteria; put only EXPLICITLY OPTIONAL qualitative phrases in preferred_criteria; route untranslatable tokens to keywords.

Respond with ONLY the JSON object — no prose, no markdown, no code fences.

SCHEMA (every field optional, omit if empty):
{
  "skills_all":          [str],   // skills the candidate MUST have (AND)
  "skills_any":          [str],   // skills where ANY match is enough (OR)
  "titles_all":          [str],   // current or historical job titles required (AND)
  "titles_any":          [str],   // current or historical job titles where ANY is enough (OR)
  "locations_country":   [str],   // canonical country names (see normalisation)
  "locations_region":    [str],   // region keys: europe | emea | north america | apac | middle east
  "min_years_experience": int,    // null if unspecified
  "graph_predicates":    [{       // graph-shaped conditions (require Neo4j)
    "type": "worked_at" | "studied_at" | "colleague_of" | "n_hop_from",
    "value": str,                 // company / school name, or candidate identifier
    "n_hops": int                 // only for n_hop_from
  }],
  "soft_criteria":       [str],   // REQUIRED qualitative phrases that need evidence, e.g. "banking treasury experience"
  "preferred_criteria":  [str],   // OPTIONAL only when explicitly hedged: "ideally", "prefer", "nice to have", "bonus"
  "keywords":            [str],   // residual tokens that didn't fit elsewhere; will run as ILIKE on cv_text
  "free_text":           str      // the original query, verbatim
}

NORMALISATION RULES:
- Country aliases: UK → "United Kingdom"; USA / US / America → "United States"; UAE → "United Arab Emirates"; KSA → "Saudi Arabia". Lower-case input matches case-insensitively.
- Regions: lowercase region keys. If user says "Europe" use locations_region: ["europe"], NOT a list of countries.
- CANDIDATE LOCATION vs COMPANY ORIGIN — a place goes in locations_country / locations_region ONLY when it is the CANDIDATE'S OWN location: "based in Dubai", "candidates in the UAE", "located in London", "UK-based candidates", "living in Europe". When a place instead describes an EMPLOYER / COMPANY — "a Western company", "a US company", "European employer", "worked at a UK firm", "experience at a Western (Europe/UK/US) company" — it is NOT a candidate location: keep it as ONE qualitative soft_criteria phrase about the company's origin and put NOTHING in locations. A parenthetical or list attached to "company"/"employer" (e.g. "Western (Europe, UK, US) company") qualifies the COMPANY, never the candidate — never extract those countries into locations.
- IGNORE the requested count: a leading "top N" / "best N" / "first N" / "show me N candidates" only says how many to return — it is NOT a filter. Never put "top 5", a bare number, or "candidates" into keywords or soft_criteria; omit it entirely.
- REQUIREMENT PRIORITY: unhedged qualities are REQUIRED. "with X", "has X", "experience in X", "must have X", and plain comma/AND lists go in soft_criteria. Put a phrase in preferred_criteria ONLY when the user explicitly says "ideally", "prefer/preferred", "nice to have", "bonus", "optional", or equivalent. Preserve the quality itself but remove the hedge from the criterion text. Never infer that a domain, skill, or experience is optional merely because it is qualitative.
- COUPLED QUALITIES: preserve relationships and parenthetical domain qualifiers as ONE atomic criterion. "Treasury experience (Banking domain)", "Treasury banking experience", and "Treasury experience within banking" become "Treasury experience within the banking domain" — never split them into independent "Treasury" and "banking" checks. Likewise keep phrases such as "payments experience in fintech" together when the domain qualifies the experience.
- Skills: keep technology names verbatim (case as given). Do not split multi-word skills ("AWS Glue", "Kubernetes Operators").
- Job titles / occupations: put role names such as "project manager", "scrum master", "data engineer" and "solutions architect" in titles_all/titles_any, NEVER in skills or soft_criteria. Use titles_all for "and" and titles_any for "or".
- Years: "5 years" / "5+ years" / "five years" → min_years_experience: 5. "senior" alone is NOT a years number — route to soft_criteria.
- Company-size phrases ("large enterprise", "Fortune 500", "FAANG", "startup", "scale-up") → soft_criteria unless explicitly hedged, then preferred_criteria.
- Industry phrases ("fintech", "healthcare", "logistics") → soft_criteria unless explicitly hedged or a specific employer is named.
- "Worked at <Company>" → graph_predicates: [{"type": "worked_at", "value": "<Company>"}]. Combine multiple "or" companies into multiple predicates.
- "in production" / "in prod" / "running production systems" → soft_criteria: ["in production"].
- Monetary / threshold constraints (salary or compensation expectation, day rate, notice period) → ONE soft_criteria phrase that keeps the subject, the operator, and the value TOGETHER, e.g. "salary expectation <= 30000 AED", "notice period <= 1 month". Normalise the operator: under / less than / below / at most / up to / max → "<="; over / more than / above / at least / min → ">=". NEVER split the number or currency from the label into separate entries, and NEVER drop the operator — a bare "salary" or a bare "30000 AED" is wrong.
- If the query is gibberish or empty, return {"free_text": "<query>"} only.

EXAMPLES

Query: "candidates with AWS Glue experience"
{"skills_all":["AWS Glue"],"free_text":"candidates with AWS Glue experience"}

Query: "project managers or scrum masters"
{"titles_any":["project manager","scrum master"],"free_text":"project managers or scrum masters"}

Query: "candidates who have worked in the UK"
{"locations_country":["United Kingdom"],"free_text":"candidates who have worked in the UK"}

Query: "candidates with 5 years experience, worked in Europe, large enterprise in production"
{"min_years_experience":5,"locations_region":["europe"],"soft_criteria":["large enterprise","in production"],"free_text":"candidates with 5 years experience, worked in Europe, large enterprise in production"}

Query: "Python and Kubernetes, worked at Google or Meta in last 3 years"
{"skills_all":["Python","Kubernetes"],"graph_predicates":[{"type":"worked_at","value":"Google"},{"type":"worked_at","value":"Meta"}],"keywords":["last 3 years"],"free_text":"Python and Kubernetes, worked at Google or Meta in last 3 years"}

Query: "senior engineers from FAANG based in London or Dublin"
{"locations_country":["United Kingdom","Ireland"],"soft_criteria":["senior","FAANG"],"keywords":["London","Dublin"],"free_text":"senior engineers from FAANG based in London or Dublin"}

Query: "data engineers asking for less than 30000 AED in salary"
{"titles_all":["data engineer"],"soft_criteria":["salary expectation <= 30000 AED"],"free_text":"data engineers asking for less than 30000 AED in salary"}

Query: "top 5 candidates with experience at a Western (Europe, UK, US) company"
{"soft_criteria":["experience at a Western (Europe/UK/US) company"],"free_text":"top 5 candidates with experience at a Western (Europe, UK, US) company"}

Query: "project manager with Treasury experience (Banking domain)"
{"titles_all":["project manager"],"soft_criteria":["Treasury experience within the banking domain"],"free_text":"project manager with Treasury experience (Banking domain)"}

Query: "project manager with Treasury experience, ideally in banking"
{"titles_all":["project manager"],"soft_criteria":["Treasury experience"],"preferred_criteria":["banking domain experience"],"free_text":"project manager with Treasury experience, ideally in banking"}

Query: "best 3 data engineers based in the UAE"
{"titles_all":["data engineer"],"locations_country":["United Arab Emirates"],"free_text":"best 3 data engineers based in the UAE"}
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
    lowered = cleaned.lower()
    return COUNTRY_ALIASES.get(
        lowered,
        _CANONICAL_COUNTRY_BY_LOWER.get(lowered, cleaned),
    )
