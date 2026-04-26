import { describe, expect, it } from 'vitest';

import { renderPrimaryScoreCell } from './candidatesUiUtils';

const baseApp = (overrides = {}) => ({
  cv_filename: 'cv.pdf',
  ...overrides,
});

describe('renderPrimaryScoreCell', () => {
  it('renders the formatted score when done', () => {
    expect(
      renderPrimaryScoreCell(
        baseApp({ cv_match_score: 82, cv_match_details: { score_scale: '0-100' }, score_status: 'done' }),
      ),
    ).toMatch(/82/);
  });

  it('shows "Scoring…" while a job is pending', () => {
    expect(
      renderPrimaryScoreCell(baseApp({ cv_match_score: null, score_status: 'pending' })),
    ).toBe('Scoring…');
  });

  it('shows "Scoring…" while a job is running, even if a prior score exists', () => {
    expect(
      renderPrimaryScoreCell(
        baseApp({ cv_match_score: 70, cv_match_details: { score_scale: '0-100' }, score_status: 'running' }),
      ),
    ).toBe('Scoring…');
  });

  it('appends "out of date" suffix when stale and a prior score exists', () => {
    const text = renderPrimaryScoreCell(
      baseApp({ cv_match_score: 75, cv_match_details: { score_scale: '0-100' }, score_status: 'stale' }),
    );
    expect(text).toMatch(/out of date/);
    expect(text).toMatch(/75/);
  });

  it('shows "Out of date" alone when stale and no prior score', () => {
    expect(
      renderPrimaryScoreCell(baseApp({ cv_match_score: null, score_status: 'stale' })),
    ).toBe('Out of date');
  });

  it('shows "Score error" when the latest job errored', () => {
    expect(
      renderPrimaryScoreCell(baseApp({ cv_match_score: null, score_status: 'error' })),
    ).toBe('Score error');
  });

  it('falls back to "Pending" when CV is uploaded but no job has run', () => {
    expect(renderPrimaryScoreCell(baseApp({ cv_match_score: null }))).toBe('Pending');
  });

  it('falls back to em-dash when no CV at all', () => {
    expect(renderPrimaryScoreCell({ cv_filename: null })).toBe('—');
  });

  it('prefers pre_screen_score over cv_match_score when both are set', () => {
    const text = renderPrimaryScoreCell(
      baseApp({ pre_screen_score: 88, cv_match_score: 50, score_status: 'done' }),
    );
    expect(text).toMatch(/88/);
    expect(text).not.toMatch(/50/);
  });
});

import {
  resolveCvMatchDetails,
  extractRequirementEvidence,
  extractRequirementKey,
} from './candidatesUiUtils';

describe('resolveCvMatchDetails', () => {
  it('prefers a completed-assessment snapshot when present', () => {
    const result = resolveCvMatchDetails({
      application: { cv_match_details: { summary: 'app v3' } },
      completedAssessment: { cv_job_match_details: { summary: 'assessment' } },
    });
    expect(result.summary).toBe('assessment');
  });

  it('falls back to v3 application.cv_match_details', () => {
    const result = resolveCvMatchDetails({
      application: { cv_match_details: { summary: 'v3' } },
    });
    expect(result.summary).toBe('v3');
  });

  it('falls back to legacy v4 application.cv_job_match_details', () => {
    const result = resolveCvMatchDetails({
      application: { cv_job_match_details: { summary: 'v4 legacy' } },
    });
    expect(result.summary).toBe('v4 legacy');
  });

  it('uses the provided fallback when no application data is available', () => {
    const result = resolveCvMatchDetails({
      application: null,
      fallback: { summary: 'role fit' },
    });
    expect(result.summary).toBe('role fit');
  });

  it('returns an empty object when nothing matches', () => {
    expect(resolveCvMatchDetails({})).toEqual({});
    expect(resolveCvMatchDetails()).toEqual({});
  });
});

describe('extractRequirementEvidence', () => {
  it('reads evidence_quote (v3 schema)', () => {
    expect(extractRequirementEvidence({ evidence_quote: 'AWS Glue and Airflow' }))
      .toBe('AWS Glue and Airflow');
  });

  it('reads cv_quote (v4 schema)', () => {
    expect(extractRequirementEvidence({ cv_quote: '5 years AWS' }))
      .toBe('5 years AWS');
  });

  it('reads evidence (legacy free-text v3)', () => {
    expect(extractRequirementEvidence({ evidence: 'Mentioned in CV' }))
      .toBe('Mentioned in CV');
  });

  it('prefers evidence_quote over older fields', () => {
    expect(extractRequirementEvidence({
      evidence_quote: 'v3',
      cv_quote: 'v4',
      evidence: 'legacy',
    })).toBe('v3');
  });

  it('returns empty string when nothing matches', () => {
    expect(extractRequirementEvidence({})).toBe('');
    expect(extractRequirementEvidence(null)).toBe('');
  });
});

describe('extractRequirementKey', () => {
  it('uses requirement_id (v3 string id)', () => {
    expect(extractRequirementKey({ requirement_id: 'req_1' }, 0)).toBe('req_1');
  });

  it('uses criterion_id (v4 int id) when no requirement_id', () => {
    expect(extractRequirementKey({ criterion_id: 42 }, 0)).toBe('42');
  });

  it('falls back to requirement+index when no id is present', () => {
    expect(extractRequirementKey({ requirement: 'Python' }, 3)).toBe('Python-3');
  });

  it('returns the index alone when nothing else is available', () => {
    expect(extractRequirementKey(null, 7)).toBe('7');
  });
});
