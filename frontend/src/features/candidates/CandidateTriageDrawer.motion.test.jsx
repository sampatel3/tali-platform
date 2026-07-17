import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MotionSystemProvider } from '../../shared/motion';
import motionFeatures from '../../shared/motion/motionFeatures';
import { CandidateTriageDrawer } from './CandidateTriageDrawer';

const candidateDetailCss = fs.readFileSync(
  path.join(process.cwd(), 'src/styles/08-candidate-detail.css'),
  'utf8',
);

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
  it('allows narrow action rows to wrap without forcing their children wider', () => {
    expect(candidateDetailCss).toMatch(/\.ctc-action-row\s*\{[^}]*flex-wrap:\s*wrap/s);
    expect(candidateDetailCss).toMatch(/\.ctc-action-row\s*>\s*\*\s*\{\s*min-width:\s*0;/);
  });

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
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
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
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    expect(screen.queryByText(/manual override/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send invite/i })).toHaveClass('taali-btn-primary');
  });

  it('keeps inherited task context visible but cannot send it from a related role', async () => {
    const onSendAssessment = vi.fn();
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={17}
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
          isRelatedRole
          agentRunning
          onSendAssessment={onSendAssessment}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    const assessmentNote = screen.getByRole('note');
    expect(assessmentNote).toHaveTextContent(/related roles are score-only/i);
    expect(assessmentNote).toHaveTextContent(/from the original role/i);
    expect(screen.getByRole('button', { name: /Backend take-home.*view only/i })).toBeDisabled();
    expect(screen.queryByText(/manual override/i)).not.toBeInTheDocument();

    const unavailableButton = screen.getByRole('button', { name: 'Available in original role' });
    expect(unavailableButton).toBeDisabled();
    expect(unavailableButton).toHaveAttribute('aria-describedby', assessmentNote.id);
    fireEvent.click(unavailableButton);
    expect(onSendAssessment).not.toHaveBeenCalled();
  });

  it('explains assessment unavailability for a related role with no shared tasks', async () => {
    const onSendAssessment = vi.fn();
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={18}
          roleTasks={[]}
          isRelatedRole
          onSendAssessment={onSendAssessment}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    expect(screen.getByText(/No shared assessment tasks are linked on the original role/i)).toBeInTheDocument();
    const unavailableButton = screen.getByRole('button', { name: 'Available in original role' });
    expect(unavailableButton).toBeDisabled();
    fireEvent.click(unavailableButton);
    expect(onSendAssessment).not.toHaveBeenCalled();
  });

  it('still sends the selected assessment from a standard role', async () => {
    const onSendAssessment = vi.fn();
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
          onSendAssessment={onSendAssessment}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));
    fireEvent.click(screen.getByRole('button', { name: 'Send invite' }));

    expect(onSendAssessment).toHaveBeenCalledOnce();
    expect(onSendAssessment).toHaveBeenCalledWith(application, '5');
  });

  it('retains inactive and unknown task links without making them sendable or part of A/B', async () => {
    const onSendAssessment = vi.fn();
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[
            { id: 5, name: 'Retired exercise', is_active: false },
            { id: 6, name: 'Malformed legacy exercise' },
            { id: 7, name: 'Approved exercise', is_active: true },
          ]}
          onSendAssessment={onSendAssessment}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));

    expect(screen.getByRole('button', { name: /Retired exercise.*retained for history/i }))
      .toBeDisabled();
    expect(screen.getByRole('button', { name: /Malformed legacy exercise.*availability unconfirmed/i }))
      .toBeDisabled();
    expect(screen.queryByRole('button', { name: /Auto.*A\/B split/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Send invite' }));
    expect(onSendAssessment).toHaveBeenCalledOnce();
    expect(onSendAssessment).toHaveBeenCalledWith(application, '7');
  });

  it('excludes inactive and unknown task links from retake choices', async () => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{ ...application, valid_assessment_id: 914 }}
          roleId={9}
          roleTasks={[
            { id: 5, name: 'Retired exercise', is_active: false },
            { id: 6, name: 'Malformed legacy exercise' },
            { id: 7, name: 'Approved exercise', is_active: true },
          ]}
          onSendAssessment={vi.fn()}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));
    fireEvent.click(screen.getByRole('button', { name: 'Send retake' }));
    fireEvent.click(screen.getByRole('button', { name: 'Task' }));

    expect(screen.getByRole('option', { name: 'Approved exercise' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Retired exercise' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Malformed legacy exercise' }))
      .not.toBeInTheDocument();
  });

  it('opens the retake dialog for an existing assessment and submits its reason', async () => {
    const onSendAssessment = vi.fn().mockResolvedValue(true);
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{ ...application, valid_assessment_id: 912 }}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
          onSendAssessment={onSendAssessment}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));
    fireEvent.click(screen.getByRole('button', { name: 'Send retake' }));

    expect(screen.getByRole('dialog', { name: 'Retake assessment' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Reason (optional)' }), {
      target: { value: 'Candidate lost connectivity' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Confirm retake' }));

    await waitFor(() => {
      expect(onSendAssessment).toHaveBeenCalledWith(
        expect.objectContaining({ id: 41, valid_assessment_id: 912 }),
        '5',
        { voidReason: 'Candidate lost connectivity' },
      );
    });
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Retake assessment' })).not.toBeInTheDocument();
    });
  });

  it('keeps a failed retake open and cancel never sends another request', async () => {
    const onSendAssessment = vi.fn().mockResolvedValue(false);
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{ ...application, valid_assessment_id: 913 }}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
          onSendAssessment={onSendAssessment}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('tab', { name: 'Send assessment' }));
    await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('id', 'candidate-action-panel-send'));
    fireEvent.click(screen.getByRole('button', { name: 'Send retake' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm retake' }));

    await waitFor(() => expect(onSendAssessment).toHaveBeenCalledOnce());
    expect(screen.getByRole('dialog', { name: 'Retake assessment' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Retake assessment' })).not.toBeInTheDocument();
    });
    expect(onSendAssessment).toHaveBeenCalledOnce();
  });

  it('closes the candidate drawer on Escape and removes the listener on unmount', () => {
    const onClose = vi.fn();
    const { unmount } = render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[]}
          onClose={onClose}
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
    unmount();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('renders an open application as read-only without falsely calling it closed', () => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[{ id: 5, name: 'Backend take-home', is_active: true }]}
          canMutate={false}
        />
      </TestMotionSystemProvider>,
    );

    expect(screen.getByRole('note')).toHaveTextContent(/Candidate actions are read-only/i);
    expect(screen.queryByText(/Application open.*No further actions/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Reject Closes the application$/i })).toBeDisabled();
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

  it('does not show an ATS warning for an unlinked candidate before handover', () => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={application}
          roleId={9}
          roleTasks={[]}
          atsProvider="workable"
        />
      </TestMotionSystemProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: /^Reject Closes the application$/i }));

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
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

  it('surfaces an orphaned ATS outcome that needs reconciliation', () => {
    render(
      <TestMotionSystemProvider>
        <CandidateTriageDrawer
          application={{
            ...application,
            integration_sync_state: {
              outcome_writeback_reconciliation: {
                status: 'manual_reconciliation_required',
                manual_reconciliation_required: true,
              },
            },
          }}
          roleId={9}
          roleTasks={[]}
          atsProvider="workable"
        />
      </TestMotionSystemProvider>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(/ATS operation needs reconciliation/i);
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
