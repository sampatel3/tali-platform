import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MotionSystemProvider } from '../../shared/motion';
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

const renderDrawer = () => render(
  <MotionSystemProvider>
    <CandidateTriageDrawer application={application} roleId={9} roleTasks={[]} />
  </MotionSystemProvider>,
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

    const sendTab = screen.getByRole('tab', { name: 'Send assessment' });
    fireEvent.click(sendTab);
    expect(sendTab).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(screen.getByRole('tabpanel', { name: 'Send assessment' })).toHaveAttribute(
      'id',
      sendTab.getAttribute('aria-controls'),
    ));

    fireEvent.click(screen.getByRole('button', { name: 'Hide details' }));
    await waitFor(() => expect(screen.queryByText('Audit history')).not.toBeInTheDocument());
  });

  it('demotes Send assessment to a manual override when the agent runs the role, keeping HITL controls', async () => {
    vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(() => {});
    render(
      <MotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home' }]}
          agentRunning
        />
      </MotionSystemProvider>,
    );

    // The decisive HITL path (Move forward, incl. Reject) stays present.
    expect(screen.getByRole('tab', { name: 'Move forward' })).toBeInTheDocument();

    const sendTab = screen.getByRole('tab', { name: 'Send assessment' });
    fireEvent.click(sendTab);
    await waitFor(() => expect(screen.getByRole('tabpanel', { name: 'Send assessment' })).toHaveAttribute(
      'id',
      sendTab.getAttribute('aria-controls'),
    ));

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
      <MotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home' }]}
        />
      </MotionSystemProvider>,
    );

    const sendTab = screen.getByRole('tab', { name: 'Send assessment' });
    fireEvent.click(sendTab);
    await waitFor(() => expect(screen.getByRole('tabpanel', { name: 'Send assessment' })).toHaveAttribute(
      'id',
      sendTab.getAttribute('aria-controls'),
    ));

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
      <MotionSystemProvider>
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
      </MotionSystemProvider>,
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
      <MotionSystemProvider>
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
      </MotionSystemProvider>,
    );

    expect(screen.getByText('Added in Taali')).toBeInTheDocument();
    expect(screen.queryByText(/rejected in Bullhorn/i)).not.toBeInTheDocument();
  });

  it('names every linked role when warning about a shared-application reject', () => {
    render(
      <MotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[]}
          atsProvider="workable"
          isRelatedRole
          roleFamily={{
            owner: { id: 31, name: 'Data Platform Lead' },
            related: [{ id: 47, name: 'AI Engineer' }],
          }}
        />
      </MotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: /^Reject Closes the application$/i }));
    expect(screen.getByRole('alert')).toHaveTextContent(/Reject everywhere/i);
    expect(screen.getByRole('alert')).toHaveTextContent(
      /shared Workable application across all linked roles: Data Platform Lead #31 \(original\) and AI Engineer #47 \(related\)/i,
    );
  });

  it('keeps the generic linked-role warning when family metadata is absent', () => {
    render(
      <MotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[]}
          atsProvider="workable"
          hasRelatedRoles
        />
      </MotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: /^Reject Closes the application$/i }));
    expect(screen.getByRole('alert')).toHaveTextContent(
      /original role and every related role/i,
    );
  });

  it('warns that moving a shared ATS application updates every linked role', () => {
    render(
      <MotionSystemProvider>
        <CandidateTriageDrawer
          application={{ ...application, workable_candidate_id: 'WK-41' }}
          roleId={9}
          roleTasks={[]}
          atsProvider="workable"
          atsStages={[{ slug: 'interview', name: 'Interview' }]}
          onMoveToAtsStage={vi.fn()}
          isRelatedRole
          roleFamily={{
            owner: { id: 31, name: 'Data Platform Lead' },
            related: [{ id: 47, name: 'AI Engineer' }],
          }}
        />
      </MotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Interview' }));

    expect(screen.getByRole('alert')).toHaveTextContent(/Shared ATS move/i);
    expect(screen.getByRole('alert')).toHaveTextContent(
      /shared Workable application to Interview updates all linked roles: Data Platform Lead #31 \(original\) and AI Engineer #47 \(related\)/i,
    );
  });

  it.each([
    ['queued', /Bullhorn rejection queued/i, false],
    ['failed', /Bullhorn rejection sync failed/i, false],
    ['confirmed', /rejected in Bullhorn/i, true],
  ])('renders the durable Bullhorn rejection receipt: %s', (status, expected, confirmed) => {
    render(
      <MotionSystemProvider>
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
      </MotionSystemProvider>,
    );

    expect(screen.getByText(expected)).toBeInTheDocument();
    if (!confirmed) {
      expect(screen.queryByText(/rejected in Bullhorn/i)).not.toBeInTheDocument();
    }
  });

  it('does not invent hired or withdrawn ATS writeback', () => {
    render(
      <MotionSystemProvider>
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
      </MotionSystemProvider>,
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
