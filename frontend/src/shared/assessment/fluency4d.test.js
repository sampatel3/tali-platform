import { describe, it, expect } from 'vitest';
import { FLUENCY_4D_AXES, rawFluency4d, readFluency4d } from './fluency4d';

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
