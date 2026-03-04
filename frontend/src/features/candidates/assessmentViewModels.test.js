import { describe, expect, it } from 'vitest';

import {
  buildAssessmentSummaryModel,
  buildRoleFitEvidenceModel,
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

    expect(model.summaryText).toBe('Composite fit 74.2 from skills 78.8.');
    expect(model.rationaleBullets).toContain('Composite fit 74.2 from skills 78.8, experience 71.5, recruiter requirements 69.0.');
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
});
