import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./RoleScreeningQuestions', () => ({
  default: () => <div data-testid="screening-question-editor" />,
}));

import { RoleAgentSettingsTab } from './RoleAgentSettingsTab';

// Minimal props: the tab renders the autonomy toggles from the role record.
const baseProps = (roleOverrides = {}) => ({
  role: { id: 1, monthly_usd_budget_cents: 5000, ...roleOverrides },
  roleCriteria: [],
  workspaceCriteria: [],
  thresholdDraft: '',
  thresholdValue: 55,
  recruiterCriteria: [],
  activeApplications: [],
  belowThresholdCount: 0,
  usageBreakdown: null,
  thresholdMode: 'manual',
});

describe('RoleAgentSettingsTab autonomy policy', () => {
  it('surfaces each positive automation grant separately', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText('Send assessments automatically')).toBeInTheDocument();
    expect(screen.getByText('Resend assessment invites automatically')).toBeInTheDocument();
    expect(screen.getByText('Advance on-policy candidates automatically')).toBeInTheDocument();
    expect(screen.getByText('Reject deterministic screening failures automatically')).toBeInTheDocument();
  });

  it('uses effective granular policy and fires the exact action change', () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab
      {...baseProps({
        auto_promote: true,
        auto_send_assessment: false,
        agent_effective_policy: { auto_send_assessment: false },
      })}
      onAutonomyChange={onAutonomyChange}
    />);
    const toggle = screen.getByRole('button', { name: 'Send assessments automatically' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_send_assessment', true);
  });

  it('previews the first-Turn-on autonomous default for an untouched role', () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab
      {...baseProps({
        auto_promote: false,
        auto_send_assessment: null,
        auto_resend_assessment: null,
        auto_advance: null,
        agent_effective_policy: {
          auto_send_assessment: false,
          auto_resend_assessment: false,
          auto_advance: false,
        },
      })}
      onAutonomyChange={onAutonomyChange}
    />);

    const send = screen.getByRole('button', { name: 'Send assessments automatically' });
    const resend = screen.getByRole('button', { name: 'Resend assessment invites automatically' });
    const advance = screen.getByRole('button', { name: 'Advance on-policy candidates automatically' });
    expect(send).toHaveAttribute('aria-pressed', 'true');
    expect(resend).toHaveAttribute('aria-pressed', 'true');
    expect(advance).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(send);
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_send_assessment', false);
  });

  it('renders one consolidated deterministic-rejection control', () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab
      {...baseProps({ auto_reject: true, auto_reject_pre_screen: false })}
      onAutonomyChange={onAutonomyChange}
    />);
    const toggle = screen.getByRole('button', { name: 'Reject deterministic screening failures automatically' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: /^Auto-reject$/i })).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(onAutonomyChange).toHaveBeenCalledWith('deterministic_pre_screen_reject', false);
  });
});

describe('RoleAgentSettingsTab reject and pause boundaries', () => {
  it('makes the irreversible human-confirm rail explicit', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(
      screen.getAllByText(/full-score and assessment reject recommendations always/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(/explicit opt-in for rules-based pre-screen failures when policy and ATS safeguards pass/i),
    ).toBeInTheDocument();
  });

  it('distinguishes manual pauses from automatic budget, credit, and startup holds', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText('AUTOMATIC HOLDS')).toBeInTheDocument();
    expect(screen.getByText(/manual Pause remains until you explicitly resume it/i)).toBeInTheDocument();
    expect(screen.getByText(/usage credits run out/i)).toBeInTheDocument();
    expect(screen.getByText(/applications close until Resume or Turn on/i)).toBeInTheDocument();
  });

  it('labels the role cap as AI usage and separates operational costs', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText(/ROLE AI-USAGE BUDGET/i)).toBeInTheDocument();
    expect(screen.getByText(/Sandbox runtime, email, storage, and repository hosting are separate/i)).toBeInTheDocument();
    expect(screen.getByText(/Settings → Billing/i)).toBeInTheDocument();
  });
});

describe('RoleAgentSettingsTab assessment task', () => {
  const catalogue = [
    { id: 700, name: 'Async Debugging Challenge' },
    { id: 701, name: 'React Component Build' },
  ];

  it('shows the currently-assigned assessment task', () => {
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[{ id: 701, name: 'React Component Build' }]}
        allTasks={catalogue}
      />,
    );
    expect(screen.getByText(/Sending/)).toBeInTheDocument();
    // The assigned task name is surfaced in the "currently sending" line.
    expect(screen.getAllByText('React Component Build').length).toBeGreaterThan(0);
    // No unassigned warning when a task is linked.
    expect(screen.queryByText('No assessment task assigned')).not.toBeInTheDocument();
  });

  it('surfaces the unassigned state clearly when no task is linked', () => {
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[]}
        allTasks={catalogue}
      />,
    );
    expect(screen.getByText('No assessment task assigned')).toBeInTheDocument();
    expect(
      screen.getByText(/Turn on will generate and validate a role-specific task automatically/i),
    ).toBeInTheDocument();
  });

  it('keeps a running taskless role in explicit skip mode until a task is chosen', () => {
    const onAutonomyChange = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps({
          agentic_mode_enabled: true,
          auto_skip_assessment: true,
        })}
        roleTasks={[]}
        allTasks={catalogue}
        onAutonomyChange={onAutonomyChange}
      />,
    );

    expect(
      screen.getByText(/this running role is skipping the assessment stage/i),
    ).toBeInTheDocument();
    const skipToggle = screen.getByRole('button', { name: 'Auto skip assessment' });
    expect(skipToggle).toBeDisabled();
    fireEvent.click(skipToggle);
    expect(onAutonomyChange).not.toHaveBeenCalled();
  });

  it('assigns a task from settings via the existing role↔task link', () => {
    const onAssignAssessmentTask = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[]}
        allTasks={catalogue}
        onAssignAssessmentTask={onAssignAssessmentTask}
      />,
    );
    // Open the assessment-task select and pick a task.
    fireEvent.click(screen.getByRole('button', { name: 'Assessment task' }));
    fireEvent.click(screen.getByRole('option', { name: 'Async Debugging Challenge' }));
    expect(onAssignAssessmentTask).toHaveBeenCalledWith(700);
  });

  it('clears the assigned task when "No assessment task" is chosen', () => {
    const onAssignAssessmentTask = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[{ id: 701, name: 'React Component Build' }]}
        allTasks={catalogue}
        onAssignAssessmentTask={onAssignAssessmentTask}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Assessment task' }));
    fireEvent.click(screen.getByRole('option', { name: 'No assessment task' }));
    expect(onAssignAssessmentTask).toHaveBeenCalledWith(null);
  });

  it('hands off multi-task A/B sets to the Job spec tab instead of a destructive single-select', () => {
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={catalogue}
        allTasks={catalogue}
      />,
    );
    expect(screen.getByText(/A\/B test/)).toBeInTheDocument();
    expect(screen.getByText(/Job spec tab/)).toBeInTheDocument();
    // No single-select in the A/B case.
    expect(screen.queryByRole('button', { name: 'Assessment task' })).not.toBeInTheDocument();
  });
});

describe('RoleAgentSettingsTab budget validation', () => {
  it('does not allow a zero-dollar role cap to be saved', () => {
    const onSaveBudget = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        onSaveBudget={onSaveBudget}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /edit/i }));
    const input = screen.getByRole('spinbutton', { name: 'Monthly budget in dollars' });
    expect(input).toHaveAttribute('min', '1');
    fireEvent.change(input, { target: { value: '0' } });
    const save = screen.getByRole('button', { name: /save cap/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(onSaveBudget).not.toHaveBeenCalled();
  });
});
