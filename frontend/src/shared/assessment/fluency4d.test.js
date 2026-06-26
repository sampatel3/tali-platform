import { describe, it, expect } from 'vitest';
import { FLUENCY_4D_AXES, rawFluency4d, readFluency4d, computeScorecard } from './fluency4d';

describe('fluency4d', () => {
  it('exposes the five ordered axes (Anthropic 4 Ds + Deliverable)', () => {
    expect(FLUENCY_4D_AXES.map((a) => a.key)).toEqual([
      'delegation',
      'description',
      'discernment',
      'diligence',
      'deliverable',
    ]);
  });

  it('reads fluency_4d nested under rubric_grading', () => {
    const assessment = {
      score_breakdown: {
        rubric_grading: {
          fluency_4d: { delegation: 80, deliverable: 60, description: null, discernment: null, diligence: null },
        },
      },
    };
    const axes = readFluency4d(assessment);
    expect(axes).not.toBeNull();
    const byKey = Object.fromEntries(axes.map((a) => [a.key, a]));
    expect(byKey.delegation).toMatchObject({ score: 80, hasSignal: true });
    expect(byKey.deliverable).toMatchObject({ score: 60, hasSignal: true });
    expect(byKey.discernment).toMatchObject({ score: null, hasSignal: false });
  });

  it('reads fluency_4d promoted to the top level of score_breakdown', () => {
    const axes = readFluency4d({ score_breakdown: { fluency_4d: { delegation: 50 } } });
    expect(axes.find((a) => a.key === 'delegation').score).toBe(50);
  });

  it('parses a score_breakdown delivered as a JSON string', () => {
    const assessment = {
      score_breakdown: JSON.stringify({ rubric_grading: { fluency_4d: { diligence: 70 } } }),
    };
    expect(readFluency4d(assessment).find((a) => a.key === 'diligence').score).toBe(70);
  });

  it('returns null when there is no rollup at all (pre-rebase assessment)', () => {
    expect(readFluency4d({ score_breakdown: { category_scores: {} } })).toBeNull();
    expect(readFluency4d({})).toBeNull();
    expect(readFluency4d(null)).toBeNull();
  });

  it('returns null when the rollup is present but every axis is null', () => {
    const assessment = {
      score_breakdown: { rubric_grading: { fluency_4d: { delegation: null, deliverable: null } } },
    };
    expect(readFluency4d(assessment)).toBeNull();
  });
});

describe('computeScorecard (the canonical 5-axis scorecard)', () => {
  it('prefers the rubric rollup per axis (source=rubric)', () => {
    const a = {
      score_breakdown: { rubric_grading: { fluency_4d: { delegation: 80, deliverable: 60 } } },
      design_thinking_score: 1, // would give a different heuristic value — must be ignored
      code_quality_score: 1,
    };
    const byKey = Object.fromEntries(computeScorecard(a).map((x) => [x.key, x]));
    expect(byKey.delegation).toMatchObject({ score: 80, source: 'rubric', hasSignal: true });
    expect(byKey.deliverable).toMatchObject({ score: 60, source: 'rubric' });
  });

  it('falls back to heuristic atomic columns when no rubric (0–10 → ×10)', () => {
    const a = { prompt_quality_score: 7, context_utilization_score: 5, written_communication_score: 6 };
    const byKey = Object.fromEntries(computeScorecard(a).map((x) => [x.key, x]));
    expect(byKey.description).toMatchObject({ score: 60, source: 'heuristic', hasSignal: true }); // mean(7,5,6)=6→60
  });

  it('mixes per-axis: rubric for graded axes, heuristic for ungraded', () => {
    const a = {
      score_breakdown: { rubric_grading: { fluency_4d: { delegation: 90, description: null } } },
      prompt_quality_score: 8, context_utilization_score: 8, written_communication_score: 8,
    };
    const byKey = Object.fromEntries(computeScorecard(a).map((x) => [x.key, x]));
    expect(byKey.delegation).toMatchObject({ score: 90, source: 'rubric' });
    expect(byKey.description).toMatchObject({ score: 80, source: 'heuristic' });
  });

  it('marks an axis with neither rubric nor heuristic as no-signal', () => {
    const byKey = Object.fromEntries(computeScorecard({ prompt_quality_score: 5 }).map((x) => [x.key, x]));
    expect(byKey.delegation).toMatchObject({ score: null, hasSignal: false });
    expect(byKey.description.hasSignal).toBe(true);
  });

  it('returns null when nothing is scorable', () => {
    expect(computeScorecard({})).toBeNull();
    expect(computeScorecard(null)).toBeNull();
  });
});
