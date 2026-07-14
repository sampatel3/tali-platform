import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MotionSystemProvider } from '../../shared/motion';
import { TranscriptPanel } from './CandidateInterviewStageViews';

const renderTranscript = (application) => render(
  <MotionSystemProvider>
    <TranscriptPanel application={application} />
  </MotionSystemProvider>,
);

describe('TranscriptPanel', () => {
  it('keeps transcript integration controls out of the candidate report', () => {
    renderTranscript({ interviews: [] });

    expect(screen.getByText('Waiting for the interview transcript.')).toBeInTheDocument();
    expect(screen.getByText(/workspace transcription service/i)).toBeInTheDocument();
    expect(screen.queryByText(/Fireflies meeting/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Paste transcript/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('labels historical manual transcripts without claiming they were synced', () => {
    renderTranscript({
      interviews: [{
        id: 17,
        stage: 'screening',
        source: 'manual',
        transcript_text: 'Legacy transcript body.',
        meeting_date: '2026-04-20T10:00:00Z',
      }],
    });

    expect(screen.getByText('Attached')).toBeInTheDocument();
    expect(screen.getByText(/historical transcript record/i)).toBeInTheDocument();
    expect(screen.queryByText('Synced')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show transcript excerpt' }));
    expect(screen.getByText('Legacy transcript body.')).toBeInTheDocument();
  });
});
