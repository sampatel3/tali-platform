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
