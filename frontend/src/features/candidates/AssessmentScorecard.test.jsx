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
    expect(axisForRubricDimension({ lens: 'practice' })).toBe('delegation');
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

  it('falls back to the heuristic signals for an axis with no rubric criteria', () => {
    render(<AssessmentScorecard assessment={ASSESSMENT} />);
    fireEvent.click(screen.getByRole('button', { name: /Deliverable/ }));
    expect(screen.getByText(/its score is the average of/)).toBeTruthy();
    expect(screen.getByText('Code Quality')).toBeTruthy();
    expect(screen.getByText('75 / 100')).toBeTruthy();
  });

  it('shows the empty state for an unscored assessment', () => {
    render(<AssessmentScorecard assessment={{}} />);
    expect(screen.getByTestId('assessment-scorecard-empty')).toBeTruthy();
  });
});
