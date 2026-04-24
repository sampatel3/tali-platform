import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CandidateWelcomePage } from './CandidateWelcomePage';

const mockPreview = vi.fn();
const mockStart = vi.fn();
const mockUploadCv = vi.fn();

vi.mock('../../shared/api', () => ({
  assessments: {
    preview: (...args) => mockPreview(...args),
    start: (...args) => mockStart(...args),
    uploadCv: (...args) => mockUploadCv(...args),
  },
}));

describe('Candidate welcome redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPreview.mockResolvedValue({
      data: {
        assessment_id: 12,
        token: 'candidate-token',
        duration_minutes: 30,
        start_gate: {
          can_start: false,
          reason: 'insufficient_credits',
          message: 'This assessment is not available yet. Please contact the hiring team to continue.',
        },
        task: {
          name: 'Debug task',
          role: 'Backend Engineer',
          duration_minutes: 30,
          calibration_enabled: false,
          has_cv_on_file: false,
        },
      },
    });
    mockStart.mockResolvedValue({ data: {} });
    mockUploadCv.mockResolvedValue({ data: {} });
  });

  it('shows the candidate-safe blocker and disables the redesigned start action', async () => {
    render(
      <CandidateWelcomePage
        token="candidate-token"
        onNavigate={vi.fn()}
        onStarted={vi.fn()}
      />,
    );

    expect(await screen.findByRole('heading', { name: /Ready to show your work/i })).toBeInTheDocument();
    expect(screen.getByText(/Please contact the hiring team to continue/i)).toBeInTheDocument();

    const startButton = screen.getByRole('button', { name: /Assessment unavailable/i });
    expect(startButton).toBeDisabled();

    await waitFor(() => expect(mockStart).not.toHaveBeenCalled());
  });
});
