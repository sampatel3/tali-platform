import { describe, expect, it } from 'vitest';

import {
  buildAssessmentSummaryModel,
  buildCandidateSnapshot,
  buildRoleFitEvidenceModel,
  buildStandingCandidateReportModel,
} from './assessmentViewModels';

describe('assessmentViewModels', () => {
  it('uses 50/50 role-fit and TAALI weighting with graceful fallbacks', () => {
    const application = {
      cv_match_score: 82,
      cv_match_details: {
        score_scale: '0-100',
        requirements_match_score_100: 74,
      },
    };

    const roleFitModel = buildRoleFitEvidenceModel({ application, completedAssessment: null });
    expect(roleFitModel.roleFitScore).toBe(78);

    const summaryModel = buildAssessmentSummaryModel({
      application,
      completedAssessment: {
        status: 'completed',
        assessment_score: 70,
        final_score: 70,
        cv_job_match_score: 82,
        cv_job_match_details: {
          score_scale: '0-100',
          requirements_match_score_100: 74,
        },
      },
    });

    expect(summaryModel.roleFitScore).toBe(78);
    expect(summaryModel.taaliScore).toBe(74);
  });

  it('renormalises role fit over present components — a null component is not weighted as 0', () => {
    // computeRoleFitScore(80, null) must be 80, not 40: the absent requirements
    // component drops out of the average rather than counting as a zero.
    const model = buildRoleFitEvidenceModel({
      application: {
        cv_match_score: 80,
        cv_match_details: { score_scale: '0-100' },
      },
      completedAssessment: null,
    });
    expect(model.roleFitScore).toBe(80);
  });

  it('does not clamp a real 0-100 assessment_score to 10 (scale-hint regression)', () => {
    // The scale hint must follow the value the ??-chain actually picks
    // (assessment_score, 0-100) — not the legacy 0-10 `score` field.
    const summaryModel = buildAssessmentSummaryModel({
      application: null,
      completedAssessment: {
        status: 'completed',
        assessment_score: 85,
        score: 8,
      },
    });
    expect(summaryModel.assessmentScore).toBe(85);
  });

  it('keeps the legacy 0-10 `score` field behaviour when it is the only source', () => {
    const summaryModel = buildAssessmentSummaryModel({
      application: null,
      completedAssessment: { status: 'completed', score: 8 },
    });
    // Behaviour is preserved verbatim: the legacy `score <= 10` path passes the
    // '0-10' hint, which CLAMPS to 0–10 (it does not ×10-scale) — so 8 stays 8.
    expect(summaryModel.assessmentScore).toBe(8);
  });

  it('yields null scores and a Pending recommendation for an unscored candidate', () => {
    // Backend serialises score_summary.assessment_score: null for candidates
    // with no completed assessment — normalizeScore(null) must stay null so the
    // rail reads "—" and the recommendation is Pending (not "Below threshold").
    const model = buildStandingCandidateReportModel({
      application: {
        cv_match_details: {},
        score_summary: { assessment_score: null, taali_score: null },
      },
      completedAssessment: null,
    });
    expect(model.summaryModel.taaliScore).toBeNull();
    expect(model.summaryModel.assessmentScore).toBeNull();
    expect(model.recommendation.label).toBe('Pending');
  });

  it('surfaces the server-canonical integrity warnings verbatim (single source)', () => {
    // Flags come ONLY from score_summary.integrity.warnings — the server's
    // build_integrity_warnings is the one place the wording lives.
    const application = {
      cv_match_score: 70,
      cv_match_details: { score_scale: '0-100', summary: 'ok' },
      score_summary: {
        integrity: {
          warnings: [
            'Hidden prompt-injection text aimed at the screener was found in the CV file (removed before scoring).',
            'CV closely mirrors the job description (62% phrase overlap).',
            '1 employer name not found verbatim in the CV text: Faketron.',
          ],
        },
      },
    };
    const model = buildRoleFitEvidenceModel({ application, completedAssessment: null });
    expect(model.integrityFlags.length).toBe(3);
    const joined = model.integrityFlags.join(' | ');
    expect(joined).toContain('prompt-injection');
    expect(joined).toContain('62% phrase overlap');
    expect(joined).toContain('Faketron');
  });

  it('does NOT re-derive integrity flags from cv_match_details — the server is the only source', () => {
    // integrity_signals present on cv_match_details, but no score_summary.integrity:
    // the FE must not compute its own warnings (one system). Result: none.
    const application = {
      cv_match_score: 70,
      cv_match_details: {
        score_scale: '0-100',
        integrity_signals: {
          document_hygiene: { injection_detected: true },
          jd_shingle: { triggered: true, similarity: 0.62 },
          unverified_employers: { count: 1, companies: ['Faketron'] },
        },
      },
    };
    const model = buildRoleFitEvidenceModel({ application, completedAssessment: null });
    expect(model.integrityFlags).toEqual([]);
  });

  it('returns no integrity flags when score_summary.integrity is absent', () => {
    const model = buildRoleFitEvidenceModel({
      application: { cv_match_score: 80, cv_match_details: { summary: 'x' } },
      completedAssessment: null,
    });
    expect(model.integrityFlags).toEqual([]);
  });

  it('exposes the server verdict + trust band + corroborations for the chip', () => {
    const application = {
      cv_match_score: 70,
      cv_match_details: { score_scale: '0-100', summary: 'ok' },
      score_summary: {
        integrity: {
          verdict: 'strong_review',
          trust_band: 'low',
          warnings: ['Timeline: Acme: ends 2018 before it starts 2020'],
          corroborations: ['GitHub profile matches the candidate named on the CV.'],
        },
      },
    };
    const model = buildRoleFitEvidenceModel({ application, completedAssessment: null });
    expect(model.integrityVerdict).toBe('strong_review');
    expect(model.integrityTrustBand).toBe('low');
    expect(model.corroborations).toEqual(['GitHub profile matches the candidate named on the CV.']);
  });

  it('derives unverified employers from cv_sections.experience[].company_unverified', () => {
    const application = {
      cv_match_score: 70,
      cv_match_details: { score_scale: '0-100', summary: 'ok' },
      cv_sections: {
        experience: [
          { company: 'Ghost Corp', company_unverified: true },
          { company: 'Real Inc', company_unverified: false },
          { company: 'Faketron', company_unverified: true },
        ],
      },
    };
    const model = buildRoleFitEvidenceModel({ application, completedAssessment: null });
    expect(model.unverifiedEmployers).toEqual(['Ghost Corp', 'Faketron']);
  });

  it('leaves the chip fields empty/null when the server sent no integrity object', () => {
    const model = buildRoleFitEvidenceModel({
      application: { cv_match_score: 80, cv_match_details: { summary: 'x' } },
      completedAssessment: null,
    });
    expect(model.integrityVerdict).toBeNull();
    expect(model.integrityTrustBand).toBeNull();
    expect(model.corroborations).toEqual([]);
    expect(model.unverifiedEmployers).toEqual([]);
  });

  it('prefers completed assessment evidence over application CV-fit data', () => {
    const application = {
      cv_match_score: 61,
      cv_match_details: {
        score_scale: '0-100',
        summary: 'Application-only summary.',
      },
      score_summary: {
        taali_score: 63,
        cv_fit_score: 61,
      },
    };

    const completedAssessment = {
      status: 'completed',
      taali_score: 88.4,
      assessment_score: 84.1,
      final_score: 84.1,
      cv_job_match_score: 79.8,
      cv_job_match_details: {
        score_scale: '0-100',
        summary: 'Completed assessment summary.',
      },
    };

    const summaryModel = buildAssessmentSummaryModel({ application, completedAssessment });

    expect(summaryModel.source.kind).toBe('assessment');
    expect(summaryModel.taaliScore).toBe(88.4);
    expect(summaryModel.assessmentScore).toBe(84.1);
    expect(summaryModel.cvFitScore).toBe(79.8);
  });

  it('sanitizes recruiter-facing /100 prose while preserving evidence details', () => {
    const model = buildRoleFitEvidenceModel({
      application: {
        cv_match_score: 74.2,
        cv_match_details: {
          score_scale: '0-100',
          summary: 'Composite fit 74.2/100 from skills 78.8/100.',
          score_rationale_bullets: [
            'Composite fit 74.2/100 from skills 78.8/100, experience 71.5/100, recruiter requirements 69.0/100.',
            'Prompt rubric stayed at 8.0/10 for communication quality.',
          ],
        },
      },
      completedAssessment: null,
    });

    // HANDOFF v2 §6 — scores normalised to integer "nn / 100".
    expect(model.summaryText).toBe('Composite fit 74 / 100 from skills 79 / 100.');
    expect(model.rationaleBullets).toContain('Composite fit 74 / 100 from skills 79 / 100, experience 72 / 100, recruiter requirements 69 / 100.');
    expect(model.rationaleBullets).toContain('Prompt rubric stayed at 8.0/10 for communication quality.');
  });

  it('builds concrete completed-assessment fallback copy from real evidence', () => {
    const summaryModel = buildAssessmentSummaryModel({
      application: null,
      completedAssessment: {
        status: 'completed',
        tests_passed: 5,
        tests_total: 6,
        score_breakdown: {
          category_scores: {
            task_completion: 8.7,
            prompt_clarity: 5.1,
          },
        },
        cv_job_match_details: {
          score_scale: '0-100',
          requirements_assessment: [
            {
              requirement: 'Direct production incident ownership',
              status: 'partially_met',
              evidence: 'Owned postmortems but not primary pager rotation.',
            },
          ],
        },
      },
    });

    expect(summaryModel.heuristicSummary).toContain('Strongest dimension: Task completion');
    expect(summaryModel.heuristicSummary).toContain('Weakest dimension to probe: Prompt clarity');
    expect(summaryModel.heuristicSummary).toContain('Passed 5 of 6 tests');
    expect(summaryModel.heuristicSummary).toContain('First recruiter requirement gap: Direct production incident ownership');
    expect(summaryModel.heuristicSummary.toLowerCase()).not.toContain('processing');
  });

  it('derives an awaiting Fireflies state for workable applications without a linked transcript', () => {
    const model = buildStandingCandidateReportModel({
      application: {
        source: 'workable',
        cv_match_score: 81,
        cv_match_details: {
          score_scale: '0-100',
          summary: 'Strong enough CV evidence to review before sending an assessment.',
        },
        screening_interview_summary: {
          fireflies: {
            status: 'awaiting_transcript',
            configured: true,
            capture_expected: true,
            invite_email: 'taali@fireflies.ai',
          },
        },
        interview_evidence_summary: {
          fireflies: {
            status: 'awaiting_transcript',
            configured: true,
            capture_expected: true,
            invite_email: 'taali@fireflies.ai',
          },
        },
      },
    });

    expect(model.firefliesModel.shouldSurface).toBe(true);
    expect(model.firefliesModel.status).toBe('awaiting_transcript');
    expect(model.firefliesModel.statusLabel).toBe('Awaiting Fireflies transcript');
    expect(model.firefliesModel.inviteEmail).toBe('taali@fireflies.ai');
  });

  it('derives linked Fireflies transcript details from interview summaries', () => {
    const model = buildStandingCandidateReportModel({
      application: {
        source: 'workable',
        cv_match_score: 84,
        cv_match_details: {
          score_scale: '0-100',
          summary: 'Strong backend role-fit evidence.',
        },
        screening_interview_summary: {
          summary: 'Screening transcript confirmed strong backend delivery.',
          latest_provider_url: 'https://fireflies.ai/view/ff-123',
          fireflies: {
            status: 'linked',
            configured: true,
            capture_expected: true,
            invite_email: 'taali@fireflies.ai',
            latest_summary: 'Screening transcript confirmed strong backend delivery.',
            latest_provider_url: 'https://fireflies.ai/view/ff-123',
            latest_meeting_date: '2026-01-14T12:00:00Z',
            latest_source: 'fireflies',
          },
        },
        interview_evidence_summary: {
          fireflies: {
            status: 'linked',
            configured: true,
            capture_expected: true,
            invite_email: 'taali@fireflies.ai',
            latest_summary: 'Screening transcript confirmed strong backend delivery.',
            latest_provider_url: 'https://fireflies.ai/view/ff-123',
            latest_meeting_date: '2026-01-14T12:00:00Z',
            latest_source: 'fireflies',
          },
        },
      },
    });

    expect(model.firefliesModel.status).toBe('linked');
    expect(model.firefliesModel.statusLabel).toBe('Stage 1 Fireflies transcript linked');
    expect(model.firefliesModel.latestSummary).toContain('strong backend delivery');
    expect(model.firefliesModel.latestProviderUrl).toBe('https://fireflies.ai/view/ff-123');
  });

  describe('buildCandidateSnapshot', () => {
    it('extracts years_experience, top_skills, and timeline from cv_match_details', () => {
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            candidate_snapshot: {
              years_experience: 12,
              top_skills: ['Python', 'dbt', 'Snowflake', 'Airflow', 'Terraform'],
              timeline: [
                { company: 'Direct Line Group', role: 'Lead Data Engineer', start_year: 2022, end_year: null, is_current: true },
                { company: 'Lloyds', role: 'Data Engineer', start_year: 2018, end_year: 2022 },
                { company: 'JPMC', role: 'Junior Data Engineer', start_year: 2015, end_year: 2018 },
              ],
            },
          },
        },
      });

      expect(snapshot).not.toBeNull();
      expect(snapshot.yearsLabel).toBe('12 yrs');
      expect(snapshot.topSkills).toEqual(['Python', 'dbt', 'Snowflake', 'Airflow', 'Terraform']);
      expect(snapshot.timeline).toHaveLength(3);
      expect(snapshot.timeline[0]).toMatchObject({
        company: 'Direct Line Group',
        role: 'Lead Data Engineer',
        range: '2022 – Present',
        isCurrent: true,
      });
      expect(snapshot.timeline[1].range).toBe('2018 – 2022');
    });

    it('falls back to matching_skills as top_skills when no snapshot block exists', () => {
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            matching_skills: ['React', 'TypeScript', 'GraphQL'],
          },
        },
      });

      expect(snapshot).not.toBeNull();
      expect(snapshot.topSkills).toEqual(['React', 'TypeScript', 'GraphQL']);
      expect(snapshot.timeline).toEqual([]);
      expect(snapshot.yearsLabel).toBeNull();
    });

    it('reads from completedAssessment.cv_job_match_details (and prompt_analytics fallback)', () => {
      // Completed-assessment payloads land at cv_job_match_details, NOT
      // cv_match_details. Mirrors the getRoleFitPayload resolver so a
      // re-scored assessment is preferred over a stale application blob.
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            candidate_snapshot: {
              years_experience: 3,
              top_skills: ['stale-skill'],
              timeline: [],
            },
          },
        },
        completedAssessment: {
          cv_job_match_details: {
            candidate_snapshot: {
              years_experience: 8,
              top_skills: ['fresh-skill'],
              timeline: [{ company: 'New Co', role: 'Lead', start_year: 2024, is_current: true }],
            },
          },
        },
      });

      expect(snapshot.yearsLabel).toBe('8 yrs');
      expect(snapshot.topSkills).toEqual(['fresh-skill']);
      expect(snapshot.timeline[0].company).toBe('New Co');
    });

    it('falls back to prompt_analytics.cv_job_match.details when cv_job_match_details is absent', () => {
      const snapshot = buildCandidateSnapshot({
        completedAssessment: {
          prompt_analytics: {
            cv_job_match: {
              details: {
                candidate_snapshot: {
                  years_experience: 5,
                  top_skills: ['nested-skill'],
                  timeline: [],
                },
              },
            },
          },
        },
      });

      expect(snapshot).not.toBeNull();
      expect(snapshot.topSkills).toEqual(['nested-skill']);
    });

    it('returns null when no usable data is present', () => {
      expect(buildCandidateSnapshot({ application: {} })).toBeNull();
      expect(buildCandidateSnapshot({ application: { cv_match_details: {} } })).toBeNull();
      expect(buildCandidateSnapshot({})).toBeNull();
    });

    it('formats sub-year experience and drops invalid timeline rows', () => {
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            candidate_snapshot: {
              years_experience: 0.5,
              top_skills: ['Go'],
              timeline: [
                { company: '', role: '' },
                { company: 'Acme', role: 'Engineer', start_year: 2024 },
              ],
            },
          },
        },
      });

      expect(snapshot.yearsLabel).toBe('<1 yr');
      expect(snapshot.timeline).toHaveLength(1);
      expect(snapshot.timeline[0]).toMatchObject({ company: 'Acme', range: '2024 – Present' });
    });

    it('derives the timeline from cv_sections, overriding the scorer snapshot timeline', () => {
      // The scorer and the CV parse disagreed on the recent employers (the
      // reported bug). cv_sections is the grounded, canonical source, so the
      // header timeline must follow it — and keep years/skills from the scorer.
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            candidate_snapshot: {
              years_experience: 10,
              top_skills: ['AWS Glue', 'PySpark'],
              timeline: [
                { company: 'Cox Communications', role: 'Data Architect', start_year: 2024, is_current: true },
                { company: 'TASK', role: 'Team Lead', start_year: 2022, end_year: 2024 },
              ],
            },
          },
          cv_sections: {
            experience: [
              { company: 'Syngenta', title: 'Lead Data Architect', start: 'Sep 2023', end: 'Present', company_unverified: false },
              { company: 'Arabian Technologies LLC', title: 'Data Engineer', start: 'Sep 2022', end: 'Sep 2023', company_unverified: true },
              { company: 'Freecharge', title: 'Data Engineer', start: '2020', end: '2022', company_unverified: false },
            ],
          },
        },
      });

      expect(snapshot.source).toBe('cv_sections');
      // Years / skills still come from the scorer snapshot block.
      expect(snapshot.yearsLabel).toBe('10 yrs');
      expect(snapshot.topSkills).toEqual(['AWS Glue', 'PySpark']);
      // Timeline is the cv_sections one — Syngenta, not Cox Communications.
      expect(snapshot.timeline.map((t) => t.company)).toEqual([
        'Syngenta', 'Arabian Technologies LLC', 'Freecharge',
      ]);
      expect(snapshot.timeline[0]).toMatchObject({ range: '2023 – Present', isCurrent: true });
      expect(snapshot.timeline[1].range).toBe('2022 – 2023');
      // The fabricated employer is carried through as unverified.
      expect(snapshot.timeline[1].companyUnverified).toBe(true);
      expect(snapshot.timeline[0].companyUnverified).toBe(false);
    });

    it('still shows a cv_sections timeline when only matching_skills exist', () => {
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: { matching_skills: ['React'] },
          cv_sections: {
            experience: [{ company: 'Globex', title: 'Engineer', start: '2021', end: 'Present' }],
          },
        },
      });
      expect(snapshot.source).toBe('cv_sections');
      expect(snapshot.topSkills).toEqual(['React']);
      expect(snapshot.timeline[0].company).toBe('Globex');
    });

    it('builds a snapshot from cv_sections alone when there is no cv_match payload', () => {
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_sections: {
            experience: [{ company: 'Initech', title: 'Architect', start: '2019', end: '' }],
          },
        },
      });
      expect(snapshot).not.toBeNull();
      expect(snapshot.timeline[0]).toMatchObject({ company: 'Initech', isCurrent: true });
    });

    it('does not let cv_sections mask a later source with real years/skills', () => {
      // An earlier source has an empty snapshot block; the cv_sections timeline
      // must NOT short-circuit on it and hide the application's real years/skills.
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            candidate_snapshot: { years_experience: 9, top_skills: ['Kafka'], timeline: [] },
          },
          cv_sections: {
            experience: [{ company: 'Globex', title: 'Engineer', start: '2021', end: 'Present' }],
          },
        },
        completedAssessment: {
          cv_job_match_details: {
            candidate_snapshot: { years_experience: null, top_skills: [], timeline: [] },
          },
        },
      });

      expect(snapshot.yearsLabel).toBe('9 yrs');
      expect(snapshot.topSkills).toEqual(['Kafka']);
      expect(snapshot.timeline[0].company).toBe('Globex');
      expect(snapshot.source).toBe('cv_sections');
    });

    it('ignores a failed cv_sections parse and falls back to the scorer timeline', () => {
      const snapshot = buildCandidateSnapshot({
        application: {
          cv_match_details: {
            candidate_snapshot: {
              years_experience: 4,
              top_skills: [],
              timeline: [{ company: 'Hooli', role: 'Dev', start_year: 2021, is_current: true }],
            },
          },
          cv_sections: { parse_failed: true, experience: [{ company: 'Garbage', title: 'x' }] },
        },
      });
      expect(snapshot.source).toBe('cv_match');
      expect(snapshot.timeline[0].company).toBe('Hooli');
    });
  });
});
