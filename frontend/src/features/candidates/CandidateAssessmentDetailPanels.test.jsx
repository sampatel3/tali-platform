import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AssessmentEvidencePanels } from './CandidateAssessmentDetailPanels';

describe('AssessmentEvidencePanels', () => {
  it('uses the shared keyboard tab contract for raw-evidence panels', () => {
    render(
      <AssessmentEvidencePanels
        candidate={{ id: 17, status: 'completed', _raw: {}, promptsList: [], timeline: [] }}
      />
    );

    const tablist = screen.getByRole('tablist', { name: 'Assessment evidence' });
    const prompts = within(tablist).getByRole('tab', { name: 'Prompts' });
    const code = within(tablist).getByRole('tab', { name: 'Code & git' });

    expect(prompts).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Prompts');

    fireEvent.keyDown(prompts, { key: 'ArrowRight' });
    expect(code).toHaveFocus();
    expect(code).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName('Code & git');
  });

  const integrityCandidate = {
    id: 18,
    status: 'completed',
    // The production shape: proctoring off, so tab_switch_count is 0 while the
    // timeline holds the real events.
    _raw: { status: 'completed', tab_switch_count: 0 },
    promptsList: [],
    timeline: [
      { event_type: 'assessment_started', timestamp: '2026-07-23T10:00:00Z' },
      { event_type: 'copy_attempt', source: 'editor', length: 12, timestamp: '2026-07-23T10:05:00Z' },
      { event_type: 'visibility_hidden', source: 'document', timestamp: '2026-07-23T10:07:00Z' },
      { event_type: 'visibility_hidden', source: 'document', timestamp: '2026-07-23T10:11:00Z' },
    ],
  };

  it('shows integrity metrics as a framed panel, not a zeroed counter', () => {
    render(<AssessmentEvidencePanels candidate={integrityCandidate} />);

    const panel = screen.getByTestId('assessment-integrity-panel');
    expect(panel).toHaveTextContent(/Assessment integrity/i);
    // The framing Sam asked for: context for review, never proof on its own.
    expect(panel).toHaveTextContent(/not proof of anything on their own/i);
    expect(panel).toHaveTextContent(/not an input to the score/i);
    expect(within(panel).getByTestId('integrity-tab-focus-times')).toBeInTheDocument();

    // The card read 0 before, because tab_switch_count is only sent when
    // proctoring is on. It now reflects the two events on the timeline.
    expect(screen.getByTestId('assessment-tab-switch-count')).toHaveTextContent('2');
  });

  it('keeps integrity metrics out of the chronological work timeline', () => {
    render(<AssessmentEvidencePanels candidate={integrityCandidate} />);

    const tablist = screen.getByRole('tablist', { name: 'Assessment evidence' });
    fireEvent.click(within(tablist).getByRole('tab', { name: 'Timeline & replay' }));

    // These used to fall through to the generic label fallback and appear as
    // rows interleaved with the candidate's actual work.
    expect(screen.queryByText(/Visibility Hidden/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Copy Attempt/i)).not.toBeInTheDocument();
  });
});
