import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MotionSystemProvider } from '../../shared/motion';
import motionFeatures from '../../shared/motion/motionFeatures';
import { CandidateTriageDrawer } from './CandidateTriageDrawer';

vi.mock('./CandidateAuditTimeline', () => ({
  CandidateAuditTimeline: () => <div>Audit history</div>,
}));

if (!HTMLElement.prototype.scrollIntoView) {
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
    configurable: true,
    writable: true,
    value: () => {},
  });
}

const application = {
  id: 41,
  candidate_id: 12,
  candidate_name: 'Maya Chen',
  candidate_email: 'maya@example.com',
  role_name: 'Data Engineer',
  pipeline_stage: 'review',
  application_outcome: 'open',
  score_summary: {},
};
const originalMatchMedia = window.matchMedia;
const TestMotionSystemProvider = ({ children }) => (
  <MotionSystemProvider features={motionFeatures}>
    {children}
  </MotionSystemProvider>
);

const renderDrawer = () => render(
  <TestMotionSystemProvider>
    <CandidateTriageDrawer application={application} roleId={9} roleTasks={[]} />
  </TestMotionSystemProvider>,
);

afterEach(() => {
  vi.restoreAllMocks();
  window.matchMedia = originalMatchMedia;
});

describe('CandidateTriageDrawer shared motion', () => {
  it('uses measured details and keyboard-safe keyed action tabs', async () => {
    vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {});
    renderDrawer();

    const details = screen.getByRole('button', { name: 'Show details' });
    expect(details).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(details);
    expect(screen.getByText('Audit history')).toBeInTheDocument();
    expect(details).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    expect(screen.getByRole('tab', { name: 'Send assessment' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    fireEvent.click(screen.getByRole('button', { name: 'Hide details' }));
    await waitFor(() => expect(screen.queryByText('Audit history')).not.toBeInTheDocument());
  });

  it('demotes Send assessment to a manual override when the agent runs the role, keeping HITL controls', async () => {
    vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {});
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home' }]}
          agentRunning
        />
      </TestMotionSystemProvider>,
    );

    // The decisive HITL path (Move forward, incl. Reject) stays present.
    expect(screen.getByRole('tab', { name: 'Move forward' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    // A quiet note flags that sending is a manual override...
    expect(screen.getByText(/manual override/i)).toBeInTheDocument();
    // ...and the Send button is demoted from primary to secondary.
    const sendBtn = screen.getByRole('button', { name: /Send invite/i });
    expect(sendBtn).toHaveClass('taali-btn-secondary');
    expect(sendBtn).not.toHaveClass('taali-btn-primary');
  });

  it('keeps Send assessment as the primary action when the agent is off', async () => {
    vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {});
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home' }]}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    expect(screen.queryByText(/manual override/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send invite/i })).toHaveClass('taali-btn-primary');
  });

  it('shows Bullhorn remote labels but submits the selected Taali intent', async () => {
    const onMoveToAtsStage = vi.fn();
    const bullhornApplication = {
      ...application,
      source: 'bullhorn',
      external_refs: { bullhorn_job_submission_id: 'BH-S-41' },
      external_stage_raw: 'Interview Scheduled',
      external_stage_normalized: 'advanced',
    };
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={bullhornApplication}
          roleId={9}
          roleTasks={[]}
          atsProvider="bullhorn"
          atsStages={[
            { slug: 'review', name: 'Client Review', kind: 'review' },
            { slug: 'advanced', name: 'Interview Scheduled', kind: 'advanced' },
          ]}
          onMoveToAtsStage={onMoveToAtsStage}
        />
      </TestMotionSystemProvider>,
    );

    expect(screen.getByText('Bullhorn')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Interview Scheduled Current stage/i })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: /^Reject Closes the application$/i }));
    expect(screen.getByRole('alert')).toHaveTextContent(
      /Interview Scheduled.*Bullhorn.*rejecting will update them there/i,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Client Review' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send to Bullhorn: Client Review' }));

    expect(onMoveToAtsStage).toHaveBeenCalledWith(
      bullhornApplication,
      'review',
      'Client Review',
    );
  });

  it('does not claim a native applicant was imported or updated in the role ATS', () => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{
            ...application,
            source: 'manual',
            application_outcome: 'rejected',
            workable_candidate_id: null,
            external_refs: null,
          }}
          roleId={9}
          roleTasks={[]}
          atsProvider="bullhorn"
        />
      </TestMotionSystemProvider>,
    );

    expect(screen.getByText('Added in Taali')).toBeInTheDocument();
    expect(screen.queryByText(/rejected in Bullhorn/i)).not.toBeInTheDocument();
  });

  it.each([
    ['queued', /Bullhorn rejection queued/i, false],
    ['failed', /Bullhorn rejection sync failed/i, false],
    ['confirmed', /rejected in Bullhorn/i, true],
  ])('renders the durable Bullhorn rejection receipt: %s', (status, expected, confirmed) => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{
            ...application,
            source: 'bullhorn',
            application_outcome: 'rejected',
            external_refs: { bullhorn_job_submission_id: 'BH-S-41' },
            integration_sync_state: {
              outcome_writeback: {
                provider: 'bullhorn',
                target_outcome: 'rejected',
                status,
              },
            },
          }}
          roleId={9}
          roleTasks={[]}
          atsProvider="bullhorn"
        />
      </TestMotionSystemProvider>,
    );

    expect(screen.getByText(expected)).toBeInTheDocument();
    if (!confirmed) {
      expect(screen.queryByText(/rejected in Bullhorn/i)).not.toBeInTheDocument();
    }
  });

  it('does not invent hired or withdrawn ATS writeback', () => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{
            ...application,
            source: 'bullhorn',
            application_outcome: 'hired',
            external_refs: { bullhorn_job_submission_id: 'BH-S-41' },
          }}
          roleId={9}
          roleTasks={[]}
          atsProvider="bullhorn"
        />
      </TestMotionSystemProvider>,
    );

    expect(screen.queryByText(/moved to hired in Bullhorn/i)).not.toBeInTheDocument();
  });

  it('uses instant native scrolling under reduced motion', () => {
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: String(query).includes('prefers-reduced-motion'),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    }));
    const scrollIntoView = vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {});

    renderDrawer();

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'auto', block: 'nearest' });
  });
});
