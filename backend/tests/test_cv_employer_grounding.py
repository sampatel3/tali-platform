"""Tests for app.cv_parsing.grounding — verifying extracted employer names
against the source CV text.

The canonical case is application 58234: a scrambled multi-column CV where
the parser emitted "Syngenta" / "Arabian Technologies LLC" and the scorer
emitted "Cox Communications" / "TASK" — yet only "Syngenta" (and the older,
unambiguous employers) actually appear in the CV text.
"""

from __future__ import annotations

from app.cv_parsing.grounding import (
    employer_is_grounded,
    ground_cv_sections,
    normalize_for_grounding,
)

# A faithful slice of the kind of word-salad the column-scrambled extraction
# produced for the reported candidate: real employer tokens present, but
# interleaved with unrelated words. Note the U+FB01 ``ﬁ`` ligature in
# "Conﬁgured" — exactly what PyPDF2 hands back for some fonts.
SCRAMBLED_CV = (
    "Built Cox Chess and travel NoSQL development Conﬁgured Syngenta and "
    "Used DynamoDB Data Engineer Freecharge Deputy Manager Boutiqaat Data "
    "Scientist STMicroelectronics Innova Cynet partitioning Arabian near "
    "management Technologies enforcement Jaypee Institute"
)


class TestNormalize:
    def test_ligature_is_decomposed(self):
        # ﬁ (U+FB01) must fold to "fi" so it matches plain ASCII.
        assert "configured" in normalize_for_grounding("Conﬁgured")

    def test_accents_and_case_fold(self):
        assert normalize_for_grounding("Nestlé S.A.") == "nestle s a"

    def test_ampersand_folds_to_and(self):
        assert normalize_for_grounding("Johnson & Johnson") == "johnson and johnson"


class TestEmployerIsGrounded:
    def setup_method(self):
        self.cv = normalize_for_grounding(SCRAMBLED_CV)

    def test_single_distinctive_token_grounds(self):
        # "Syngenta" is a real token in the text.
        assert employer_is_grounded("Syngenta", self.cv) is True

    def test_legal_suffix_is_ignored(self):
        # CV says "Freecharge"; the name carries a "Pvt. Ltd." the CV omits.
        assert employer_is_grounded("Freecharge Pvt. Ltd.", self.cv) is True

    def test_multiword_name_absent_is_not_grounded(self):
        # "Cox" appears (in "Cox Chess"), but "Cox Communications" does not.
        assert employer_is_grounded("Cox Communications", self.cv) is False

    def test_scattered_tokens_do_not_ground(self):
        # "Arabian" and "Technologies" both appear, but never adjacent —
        # the descriptive word must not be stripped to rescue a bad guess.
        assert employer_is_grounded("Arabian Technologies LLC", self.cv) is False

    def test_other_real_employers_ground(self):
        for name in ("Boutiqaat", "STMicroelectronics", "Innova", "Cynet"):
            assert employer_is_grounded(name, self.cv) is True, name

    def test_ampersand_variants_match(self):
        cv = normalize_for_grounding("Worked at Johnson and Johnson on QA")
        assert employer_is_grounded("Johnson & Johnson", cv) is True

    def test_empty_company_is_not_grounded(self):
        assert employer_is_grounded("   ", self.cv) is False

    def test_company_of_only_legal_tokens_is_not_grounded(self):
        assert employer_is_grounded("LLC", self.cv) is False

    def test_no_cv_text_does_not_flag(self):
        # With nothing to check against we must not declare everything fake.
        assert employer_is_grounded("Cox Communications", "") is True

    def test_dotted_legal_suffix_is_tolerated(self):
        # "S.A." / "S.A.S." normalize to single-letter tokens — they must still
        # be stripped so the name grounds against a CV that omits the suffix.
        cv = normalize_for_grounding("Worked at Acme on data platforms")
        assert employer_is_grounded("Acme S.A.", cv) is True
        assert employer_is_grounded("Acme S.A.S.", cv) is True

    def test_initials_are_not_mistaken_for_a_suffix(self):
        # "J.P. Morgan" -> "j p morgan": single letters that don't spell a
        # legal form must be kept, so the full name is still required.
        cv = normalize_for_grounding("Spent three years at J.P. Morgan in NY")
        assert employer_is_grounded("J.P. Morgan", cv) is True
        assert employer_is_grounded("J.P. Goldman", cv) is False


class TestGroundCvSections:
    def _blob(self):
        return {
            "experience": [
                {"company": "Syngenta", "title": "Lead Data Architect",
                 "start": "Sep 2023", "end": "Present",
                 "bullets": ["Led data platform architecture"]},
                {"company": "Arabian Technologies LLC", "title": "Data Engineer",
                 "start": "Sep 2022", "end": "Sep 2023",
                 "bullets": ["Built AWS ETL pipelines"]},
                {"company": "Freecharge", "title": "Data Engineer",
                 "start": "2020", "end": "2022", "bullets": []},
            ]
        }

    def test_flags_only_ungrounded_employers(self):
        blob = self._blob()
        flagged = ground_cv_sections(blob, SCRAMBLED_CV)

        assert [item["company"] for item in flagged] == ["Arabian Technologies LLC"]
        exp = blob["experience"]
        assert exp[0]["company_unverified"] is False   # Syngenta — real
        assert exp[1]["company_unverified"] is True     # Arabian — fabricated
        assert exp[2]["company_unverified"] is False   # Freecharge — real

    def test_preserves_company_string_and_evidence(self):
        blob = self._blob()
        ground_cv_sections(blob, SCRAMBLED_CV)
        flagged_entry = blob["experience"][1]
        # The name is kept (UI marks it "unverified"); title/dates/bullets intact.
        assert flagged_entry["company"] == "Arabian Technologies LLC"
        assert flagged_entry["title"] == "Data Engineer"
        assert flagged_entry["bullets"] == ["Built AWS ETL pipelines"]

    def test_is_idempotent(self):
        blob = self._blob()
        first = ground_cv_sections(blob, SCRAMBLED_CV)
        second = ground_cv_sections(blob, SCRAMBLED_CV)
        assert first == second

    def test_empty_cv_text_flags_nothing(self):
        blob = self._blob()
        flagged = ground_cv_sections(blob, "")
        assert flagged == []
        assert all("company_unverified" not in e for e in blob["experience"])

    def test_blank_company_marked_verified(self):
        blob = {"experience": [{"company": "", "title": "Consultant", "bullets": []}]}
        ground_cv_sections(blob, SCRAMBLED_CV)
        assert blob["experience"][0]["company_unverified"] is False
