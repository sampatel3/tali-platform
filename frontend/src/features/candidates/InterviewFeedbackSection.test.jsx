import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { InterviewFeedbackSection } from './InterviewFeedbackSection';

// The "Interview feedback" section on the prep tab lists recorded entries
// newest-first with a recommendation badge and expandable detail, and the
// "Record feedback" form POSTs via the roles API then refreshes the list.

const interviewKit = {
  priority_probes: [
    { criterion_id: 'c1', criterion_text: 'System design depth' },
  ],
  knockout_checks: [
    { criterion_id: 'c2', criterion_text: 'On-call experience' },
  ],
};

const existingEntry = {
  id: 11,
  application_id: 5,
  role_id: 3,
  interview_round: 'technical',
  interviewer_name: 'Dana Recruiter',
  overall_recommendation: 'yes',
  dimension_ratings: { delegation: 4 },
  probe_results: [
    { criterion_id: 'c1', criterion_text: 'System design depth', result: 'confirmed' },
  ],
  notes: 'Strong on design.',
  created_at: new Date().toISOString(),
};

const makeApi = (overrides = {}) => ({
  createInterviewFeedback: vi.fn().mockResolvedValue({ data: { id: 99 } }),
  listInterviewFeedback: vi.fn().mockResolvedValue({ data: [] }),
  updateInterviewFeedback: vi.fn().mockResolvedValue({ data: {} }),
  deleteInterviewFeedback: vi.fn().mockResolvedValue({ data: {} }),
  ...overrides,
});

const renderSection = (props = {}) => render(
  <InterviewFeedbackSection
    applicationId={5}
    interviewKit={interviewKit}
    initialFeedback={props.initialFeedback ?? []}
    rolesApi={props.rolesApi ?? makeApi()}
    {...props}
  />
);

describe('InterviewFeedbackSection', () => {
  it('renders an empty state when there is no feedback yet', () => {
    renderSection();
    expect(screen.getByText(/No interview feedback recorded yet/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Record feedback/i })).toBeInTheDocument();
  });

  it('lists existing entries with round, interviewer and recommendation', () => {
    renderSection({ initialFeedback: [existingEntry] });
    expect(screen.getByText('Technical')).toBeInTheDocument();
    expect(screen.getByText(/Dana Recruiter/)).toBeInTheDocument();
    // Recommendation badge label.
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('submits the form and calls createInterviewFeedback then refreshes', async () => {
    const api = makeApi({ listInterviewFeedback: vi.fn().mockResolvedValue({ data: [existingEntry] }) });
    renderSection({ rolesApi: api });

    // Open the form.
    fireEvent.click(screen.getByRole('button', { name: /Record feedback/i }));

    // A recommendation is required to submit — pick the "Strong yes" chip.
    fireEvent.click(screen.getByRole('button', { name: 'Strong yes' }));

    // Save.
    fireEvent.click(screen.getByRole('button', { name: /Save feedback/i }));

    await waitFor(() => expect(api.createInterviewFeedback).toHaveBeenCalledTimes(1));
    const [appId, payload] = api.createInterviewFeedback.mock.calls[0];
    expect(appId).toBe(5);
    expect(payload.overall_recommendation).toBe('strong_yes');
    // Round defaults to screening; probes auto-populate from the kit (de-duped to 2).
    expect(payload.interview_round).toBe('screening');
    expect(payload.probe_results).toHaveLength(2);

    // The list is refreshed after a successful save.
    await waitFor(() => expect(api.listInterviewFeedback).toHaveBeenCalled());
  });

  it('blocks submit until a recommendation is chosen', () => {
    const api = makeApi();
    renderSection({ rolesApi: api });
    fireEvent.click(screen.getByRole('button', { name: /Record feedback/i }));
    // Save button is disabled with no recommendation selected.
    expect(screen.getByRole('button', { name: /Save feedback/i })).toBeDisabled();
    expect(api.createInterviewFeedback).not.toHaveBeenCalled();
  });

  it('deletes an entry via the API', async () => {
    const api = makeApi({ listInterviewFeedback: vi.fn().mockResolvedValue({ data: [] }) });
    renderSection({ initialFeedback: [existingEntry], rolesApi: api });
    fireEvent.click(screen.getByRole('button', { name: /^Delete$/i }));
    await waitFor(() => expect(api.deleteInterviewFeedback).toHaveBeenCalledWith(5, 11));
  });

  it('read-only mode (recruiter share links) lists entries without record/edit/delete', () => {
    renderSection({ initialFeedback: [existingEntry], readOnly: true });
    expect(screen.getByText(/Dana Recruiter/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Record feedback/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Edit$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Delete$/i })).not.toBeInTheDocument();
  });
});
