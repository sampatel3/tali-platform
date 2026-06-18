"""Ground extracted employer names against the source CV text.

Why this exists
---------------
Two independent LLM passes read the same ``cv_text``: the CV-section parser
(writes ``cv_sections``, rendered on the candidate CV tab) and the scorer
(writes ``cv_match_details.candidate_snapshot``, rendered as the header
"Recent roles"). When PDF text extraction scrambles a multi-column CV into
word-salad, each pass *guesses* the ambiguous recent employers — and they
guess differently. The result seen in the wild (application 58234): the
header showed "Cox Communications" / "TASK" while the CV tab showed
"Syngenta" / "Arabian Technologies LLC" for the same person — and three of
those four employer names never appear in the CV at all.

This module enforces one invariant: **an employer name is only trustworthy
if it appears in the CV text** (modulo casing, punctuation, ligatures,
accents and a trailing legal suffix). Names that aren't found are kept but
flagged ``company_unverified`` so the UI can mark them rather than present a
fabricated employer as fact. The job title, dates and bullets are always
left untouched — they're real evidence; only the employer *attribution* is
in question.

Deterministic and LLM-free, so it runs on every parse and can re-ground
already-stored rows in a backfill without re-scoring.
"""

from __future__ import annotations

import re
import unicodedata

# Trailing legal / corporate-form tokens the LLM may append (or that the CV
# may omit). Stripped from the END of a company name before matching so
# "Freecharge Pvt. Ltd." grounds against a CV that just writes "Freecharge".
# Deliberately conservative: only true legal forms, never descriptive words
# like "Technologies" / "Communications" / "Solutions" / "Group" — those are
# the distinctive part of the name and dropping them would let a fabricated
# "Arabian Technologies" match a stray "Arabian" elsewhere in the text.
_LEGAL_SUFFIX_TOKENS = frozenset(
    {
        "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "pvt",
        "pte", "plc", "corp", "corporation", "gmbh", "ag", "sa", "sas", "nv",
        "bv", "oy", "ab", "as", "srl", "spa", "sl", "kk", "kg", "kft", "doo",
        "sro", "sdn", "bhd",
    }
)


def normalize_for_grounding(text: str) -> str:
    """Casefold + de-accent + de-ligature + strip punctuation to a single
    space-delimited token stream.

    NFKD decomposition turns ligatures into ASCII (the real CV text contains
    ``Conﬁgured`` with a U+FB01 ``ﬁ`` ligature) and splits accents into
    combining marks we then drop, so "Nestlé" and "Nestle" compare equal.
    ``&`` is folded to ``and`` so "Johnson & Johnson" and "Johnson and
    Johnson" match either way round.
    """
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_marks.lower().replace("&", " and ")
    alnum = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", alnum).strip()


def _significant_tokens(company: str) -> list[str]:
    """Normalized company tokens with trailing legal-form tokens removed.

    Handles both whole-token forms ("ltd", "gmbh") and dotted forms that
    normalize to single letters ("S.A." -> "s a", "S.A.S." -> "s a s"): a
    trailing run of single-character tokens is joined and stripped when it
    spells a known suffix, so "Acme S.A." grounds against a CV that just says
    "Acme". Single letters that don't spell a suffix (initials like "J P
    Morgan") are left intact.
    """
    tokens = normalize_for_grounding(company).split()
    while tokens:
        if tokens[-1] in _LEGAL_SUFFIX_TOKENS:
            tokens.pop()
            continue
        stripped = False
        for k in range(min(4, len(tokens)), 1, -1):
            tail = tokens[-k:]
            if all(len(t) == 1 for t in tail) and "".join(tail) in _LEGAL_SUFFIX_TOKENS:
                del tokens[-k:]
                stripped = True
                break
        if not stripped:
            break
    return tokens


def employer_is_grounded(company: str, normalized_cv: str) -> bool:
    """True when ``company``'s significant tokens appear, contiguous and
    whole-token, in the already-normalized CV text.

    Whole-token containment (space-padded) keeps "Cox Communications" from
    matching a CV that only says "Cox" inside an unrelated phrase, while a
    single distinctive token ("Syngenta") still grounds.
    """
    if not normalized_cv:
        # No text to verify against — don't flag everything as fabricated.
        return True
    tokens = _significant_tokens(company)
    if not tokens:
        return False
    needle = " " + " ".join(tokens) + " "
    haystack = " " + normalized_cv + " "
    return needle in haystack


def ground_cv_sections(blob: dict, cv_text: str) -> list[dict]:
    """Flag unverifiable employers on a parsed ``cv_sections`` blob in place.

    For each ``experience`` entry, sets ``company_unverified`` to True when
    the company name can't be found in ``cv_text`` (and False when it can, so
    the flag is always explicit). The company string itself is preserved.

    Returns a list of ``{"index", "company"}`` for the flagged entries, for
    logging / audit. A no-op (returns ``[]``) when there's no CV text to
    check against, so we never blanket-flag on missing input.
    """
    if not isinstance(blob, dict):
        return []
    experience = blob.get("experience")
    if not isinstance(experience, list):
        return []
    normalized_cv = normalize_for_grounding(cv_text)
    if not normalized_cv:
        return []

    flagged: list[dict] = []
    for index, entry in enumerate(experience):
        if not isinstance(entry, dict):
            continue
        company = str(entry.get("company") or "").strip()
        if not company:
            entry["company_unverified"] = False
            continue
        if employer_is_grounded(company, normalized_cv):
            entry["company_unverified"] = False
        else:
            entry["company_unverified"] = True
            flagged.append({"index": index, "company": company})
    return flagged
