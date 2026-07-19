import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import { AssessmentScorecard, readGradedRubricDimensions } from './AssessmentScorecard';
import { axisForRubricDimension } from '../../shared/assessment/fluency4d';

const ASSESSMENT = {
  evaluation_rubric: {
    debugging_strategy: { weight: 0.3, lens: 'discernment' },
    release_safety: { weight: 0.3, lens: 'diligence' },
    design_decisions: { weight: 0.4 },
  },
  score_breakdown: {
    rubric_grading: {
      dimensions: [
        {
          id: 'debugging_strategy',
          score: 6,
          rating: 'good',
          reasoning: 'Narrowed the failing webhook test quickly.',
          evidence_citations: ['transcript turn 4'],
        },
        {
          id: 'release_safety',
          score: 8,
          rating: 'excellent',
          reasoning: 'Verified before claiming done.',
          evidence_citations: [],
        },
        {
          id: 'design_decisions',
          score: 7,
          rating: 'good',
          reasoning: 'Owned the load-bearing calls.',
          evidence_citations: [],
        },
      ],
      fluency_4d: {
        delegation: 70,
        description: 81,
        discernment: 60,
        diligence: 80,
        deliverable: null,
      },
    },
  },
  code_quality_score: 7.5,
};

describe('axisForRubricDimension', () => {
  it('mirrors the backend lens→axis mapping with delegation as the default', () => {
    expect(axisForRubricDimension({ lens: 'discernment' })).toBe('discernment');
    expect(axisForRubricDimension({ lens: 'deliverable' })).toBe('deliverable');
    expect(axisForRubricDimension({ fluency: 'description', lens: 'diligence' })).toBe('description');
    expect(axisForRubricDimension({ grader: 'interrogation_outcome', lens: 'diligence' })).toBe('delegation');
    expect(axisForRubricDimension({})).toBe('delegation');
    expect(axisForRubricDimension(null)).toBe('delegation');
    // Both of these are real lenses the spec validator accepts. They used to
    // reach the back-compat default; the map now routes them deliberately.
    expect(axisForRubricDimension({ lens: 'decision' })).toBe('delegation');
    expect(axisForRubricDimension({ lens: 'practice' })).toBe('description');
    expect(axisForRubricDimension({ grader: 'practice_outcome' })).toBe('description');
  });
});

describe('readGradedRubricDimensions', () => {
  it('reads criteria with citations and tolerates a JSON-string breakdown', () => {
    const fromString = readGradedRubricDimensions({
      score_breakdown: JSON.stringify(ASSESSMENT.score_breakdown),
    });
    expect(fromString).toHaveLength(3);
    expect(fromString[0].citations).toEqual(['transcript turn 4']);
    expect(readGradedRubricDimensions({ score_breakdown: 'not json' })).toEqual([]);
  });

  it('synthesizes honest per-dimension errors when the grader failed before returning grades', () => {
    const dimensions = readGradedRubricDimensions({
      score_breakdown: {
        rubric_grading: {
          status: 'failed',
          fully_graded: false,
          failed_dimension_ids: ['quality', 'judgment'],
          error: 'rubric_grader_unavailable',
        },
      },
    });
    expect(dimensions.map((dimension) => dimension.id)).toEqual(['quality', 'judgment']);
    expect(dimensions.every((dimension) => dimension.score == null)).toBe(true);
    expect(dimensions.every((dimension) => dimension.error === 'rubric_grader_unavailable')).toBe(true);
  });
});

describe('AssessmentScorecard', () => {
  it('renders the five axes and expands one into its graded criteria', () => {
    render(<AssessmentScorecard assessment={ASSESSMENT} />);
    expect(screen.getByText('SCORECARD · THE 5 Ds')).toBeTruthy();
    ['Delegation', 'Description', 'Discernment', 'Diligence', 'Deliverable'].forEach((label) => {
      expect(screen.getByText(label)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /Discernment/ }));
    expect(screen.getByText('Debugging Strategy')).toBeTruthy();
    expect(screen.getByText('Narrowed the failing webhook test quickly.')).toBeTruthy();
    expect(screen.getByText('transcript turn 4')).toBeTruthy();
    expect(screen.getByText('60 / 100')).toBeTruthy();
  });

  it('shows an ungraded axis as "—" rather than borrowing a heuristic', () => {
    render(<AssessmentScorecard assessment={ASSESSMENT} />);
    // fluency_4d.deliverable is null and code_quality_score is deliberately not
    // a telemetry source (it's a hardcoded constant), so this axis has nothing.
    const deliverable = screen.getByRole('button', { name: /Deliverable/ });
    expect(deliverable.textContent).toContain('—');
    expect(deliverable.textContent).not.toContain('/100');

    fireEvent.click(deliverable);
    expect(screen.getByText(/No signal captured for this dimension yet/)).toBeTruthy();
  });

  it('labels heuristic telemetry as not-a-grade under an ungraded axis', () => {
    // Description has no rubric grade here, but does have heuristic columns.
    const assessment = {
      ...ASSESSMENT,
      score_breakdown: {
        rubric_grading: {
          ...ASSESSMENT.score_breakdown.rubric_grading,
          fluency_4d: { ...ASSESSMENT.score_breakdown.rubric_grading.fluency_4d, description: null },
        },
      },
      prompt_quality_score: 7.5,
    };
    render(<AssessmentScorecard assessment={assessment} />);
    const description = screen.getByRole('button', { name: /Description/ });
    expect(description.textContent).toContain('—');

    fireEvent.click(description);
    expect(screen.getByText(/Not graded/)).toBeTruthy();
    expect(screen.getByText(/they are not a grade/)).toBeTruthy();
    expect(screen.getByText('Prompt Quality')).toBeTruthy();
    // Rendered as a bare signal value, not an "nn / 100" score.
    expect(screen.getByText('75')).toBeTruthy();
    expect(screen.queryByText('75 / 100')).toBeNull();
  });

  it('renders rating chips on the purple scale, not the success/danger design-system colours', () => {
    render(<AssessmentScorecard assessment={ASSESSMENT} />);
    fireEvent.click(screen.getByRole('button', { name: /Discernment/ }));
    const chip = screen.getByText('good');
    // Per-criterion ratings are evidence, not verdicts — no green/red badge classes.
    expect(chip.className).not.toMatch(/taali-badge-(success|danger|info)/);
    expect(chip.getAttribute('style') || '').toMatch(/color-mix/);
    expect(chip.getAttribute('style') || '').toMatch(/--purple/);
  });

  it('shows the empty state for an unscored assessment', () => {
    render(<AssessmentScorecard assessment={{}} />);
    expect(screen.getByTestId('assessment-scorecard-empty')).toBeTruthy();
  });

  it('surfaces partial grading errors without rendering them as zero scores', () => {
    const partial = {
      ...ASSESSMENT,
      scoring_partial: true,
      score_breakdown: {
        rubric_grading: {
          status: 'partial',
          fully_graded: false,
          fluency_4d: ASSESSMENT.score_breakdown.rubric_grading.fluency_4d,
          dimensions: [
            ...ASSESSMENT.score_breakdown.rubric_grading.dimensions,
            {
              id: 'provider_failed_dimension',
              score: 0,
              rating: 'error',
              reasoning: 'Grading is incomplete.',
              error: 'insufficient credits',
            },
          ],
        },
      },
      evaluation_rubric: {
        ...ASSESSMENT.evaluation_rubric,
        provider_failed_dimension: { lens: 'discernment' },
      },
    };

    const dimensions = readGradedRubricDimensions(partial);
    const failed = dimensions.find((dimension) => dimension.id === 'provider_failed_dimension');
    expect(failed.score).toBeNull();
    expect(failed.error).toBe('insufficient credits');

    render(<AssessmentScorecard assessment={partial} />);
    expect(screen.getByTestId('assessment-grading-pending')).toBeTruthy();
    expect(screen.getByText(/Provider Failed Dimension: insufficient credits/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Discernment/ }));
    expect(screen.getByText('pending')).toBeTruthy();
    expect(screen.queryByText('0 / 100')).toBeNull();
  });
});
