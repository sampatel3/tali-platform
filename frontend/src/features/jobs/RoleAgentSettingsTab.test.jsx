import { act, fireEvent, render, screen } from '@testing-library/react';
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
  it('surfaces each automatic action separately', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText('Auto-reject pre-screen failures')).toBeInTheDocument();
    expect(screen.getByText('Auto-reject after scoring')).toBeInTheDocument();
    expect(screen.getByText('Auto-send assessments')).toBeInTheDocument();
    expect(screen.getByText('Auto-retry assessment invites')).toBeInTheDocument();
    expect(screen.getByText('Auto-advance qualified candidates')).toBeInTheDocument();
  });

  it('uses effective granular policy and fires the exact action change', async () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab
      {...baseProps({
        auto_promote: true,
        auto_send_assessment: false,
        agent_effective_policy: { auto_send_assessment: false },
      })}
      onAutonomyChange={onAutonomyChange}
    />);
    const toggle = screen.getByRole('button', { name: 'Auto-send assessments' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_send_assessment', true);
  });

  it('paints the clicked value immediately and blocks overlapping switch saves', async () => {
    let resolveSave;
    const onAutonomyChange = vi.fn(() => new Promise((resolve) => {
      resolveSave = resolve;
    }));
    render(<RoleAgentSettingsTab
      {...baseProps({
        auto_promote: true,
        auto_send_assessment: true,
        auto_resend_assessment: true,
        auto_advance: true,
      })}
      onAutonomyChange={onAutonomyChange}
    />);

    const send = screen.getByRole('button', { name: 'Auto-send assessments' });
    const retry = screen.getByRole('button', { name: 'Auto-retry assessment invites' });
    const advance = screen.getByRole('button', { name: 'Auto-advance qualified candidates' });
    expect(send).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(send);

    // Optimistic paint happens before the deferred request settles, while a
    // synchronous mutex prevents another switch from reusing this revision.
    expect(send).toHaveAttribute('aria-pressed', 'false');
    expect(send.closest('label')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Saving…')).toBeInTheDocument();
    expect(send).toBeDisabled();
    expect(retry).toBeDisabled();
    expect(advance).toBeDisabled();

    fireEvent.click(advance);
    fireEvent.click(send);
    expect(onAutonomyChange).toHaveBeenCalledTimes(1);
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_send_assessment', false);

    await act(async () => {
      resolveSave();
    });

    expect(screen.queryByText('Saving…')).not.toBeInTheDocument();
    expect(send).not.toBeDisabled();
    expect(retry).not.toBeDisabled();
    expect(advance).not.toBeDisabled();
  });

  it('previews safe HITL defaults for an untouched role', async () => {
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

    const send = screen.getByRole('button', { name: 'Auto-send assessments' });
    const resend = screen.getByRole('button', { name: 'Auto-retry assessment invites' });
    const advance = screen.getByRole('button', { name: 'Auto-advance qualified candidates' });
    expect(send).toHaveAttribute('aria-pressed', 'false');
    expect(resend).toHaveAttribute('aria-pressed', 'false');
    expect(advance).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Auto-reject pre-screen failures' }))
      .toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Auto-reject after scoring' }))
      .toHaveAttribute('aria-pressed', 'false');

    await act(async () => {
      fireEvent.click(send);
    });
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_send_assessment', true);
  });

  it('renders the ordinary Agent settings experience for a related role', () => {
    render(<RoleAgentSettingsTab
      {...baseProps({
        name: 'AI Platform Engineer',
        role_kind: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer',
      })}
      onAutonomyChange={vi.fn()}
    />);

    expect(screen.getByText('HOW THE AGENT RUNS THIS ROLE')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Screening threshold' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Assessment tasks' })).toBeInTheDocument();
    expect(screen.getByTestId('screening-question-editor')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Auto-send assessments' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-advance qualified candidates' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-reject pre-screen failures' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Auto-reject after scoring' })).toBeDisabled();
    expect(screen.getByText('RECRUITER APPROVAL')).toBeInTheDocument();
    expect(screen.getByText(
      'AI Platform Engineer #1 shares one ATS application with AI Engineer #77, so its candidate actions remain behind recruiter approval.',
    )).toBeInTheDocument();
    expect(screen.getByText('Shared-pool candidate actions remain behind recruiter approval.')).toBeInTheDocument();
    expect(screen.getByText('PAUSE BEHAVIOR')).toBeInTheDocument();
    expect(screen.queryByText(/Related-role scoring/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Open original role settings/i })).not.toBeInTheDocument();
  });

  it('shows the persisted assessment-skip state when a related role has an active task', () => {
    render(<RoleAgentSettingsTab
      {...baseProps({
        name: 'AI Platform Engineer',
        role_kind: 'sister',
        ats_owner_role_id: 77,
        ats_owner_role_name: 'AI Engineer',
        auto_skip_assessment: false,
      })}
      roleTasks={[{ id: 10, name: 'AI production readiness', is_active: true }]}
      allTasks={[{ id: 10, name: 'AI production readiness', is_active: true }]}
      onAutonomyChange={vi.fn()}
    />);

    expect(screen.getByRole('button', { name: 'Skip assessment stage' }))
      .toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Skip assessment stage' })).toBeDisabled();
  });

  it('persists pre-screen and scored rejection controls independently', async () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab
      {...baseProps({ auto_reject: true, auto_reject_pre_screen: false })}
      onAutonomyChange={onAutonomyChange}
    />);
    const preScreen = screen.getByRole('button', { name: 'Auto-reject pre-screen failures' });
    const scored = screen.getByRole('button', { name: 'Auto-reject after scoring' });
    expect(preScreen).toHaveAttribute('aria-pressed', 'false');
    expect(scored).toHaveAttribute('aria-pressed', 'true');
    await act(async () => {
      fireEvent.click(preScreen);
    });
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_reject_pre_screen', true);
  });
});

describe('RoleAgentSettingsTab reject and pause boundaries', () => {
  it('makes the irreversible human-confirm rail explicit', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(
      screen.getAllByText(/Assessment-stage and LLM-only rejects/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(/fail a required screening question or the cheap pre-screen gate/i),
    ).toBeInTheDocument();
  });

  it('distinguishes manual pauses from automatic budget, credit, and startup holds', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText('PAUSE BEHAVIOR')).toBeInTheDocument();
    expect(screen.getByText(/manual pause waits for you to resume it/i)).toBeInTheDocument();
    expect(screen.getByText(/Budget, credit, and startup holds recover automatically/i)).toBeInTheDocument();
    expect(screen.getByText(/applications close until Resume or Turn on/i)).toBeInTheDocument();
  });

  it('labels the role cap as AI usage and separates operational costs', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText(/ROLE AI-USAGE BUDGET/i)).toBeInTheDocument();
    expect(screen.getByText(/Other operating costs appear in Settings/i)).toBeInTheDocument();
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
    expect(screen.getByText('1 task assigned')).toBeInTheDocument();
    // The assigned task name is surfaced in the compact selection summary.
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
      screen.getByText(/Candidates will skip the assessment stage until you assign an active task/i),
    ).toBeInTheDocument();
    const skipToggle = screen.getByRole('button', { name: 'Skip assessment stage' });
    expect(skipToggle).toHaveAttribute('aria-pressed', 'true');
    expect(skipToggle).toBeDisabled();
  });

  it('keeps every taskless role in explicit skip mode until a task is chosen', () => {
    const onAutonomyChange = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps({
          agentic_mode_enabled: false,
          auto_skip_assessment: false,
        })}
        roleTasks={[]}
        allTasks={catalogue}
        onAutonomyChange={onAutonomyChange}
      />,
    );

    expect(
      screen.getByText(/Candidates will skip the assessment stage until you assign an active task/i),
    ).toBeInTheDocument();
    const skipToggle = screen.getByRole('button', { name: 'Skip assessment stage' });
    expect(skipToggle).toBeDisabled();
    fireEvent.click(skipToggle);
    expect(onAutonomyChange).not.toHaveBeenCalled();
  });

  it('assigns a task by sending the complete selected ID set', async () => {
    const onAssignAssessmentTasks = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[]}
        allTasks={catalogue}
        onAssignAssessmentTasks={onAssignAssessmentTasks}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: 'Async Debugging Challenge' }));
    });
    expect(onAssignAssessmentTasks).toHaveBeenCalledWith([700]);
  });

  it('clears the assigned task without a destructive single-select', async () => {
    const onAssignAssessmentTasks = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[{ id: 701, name: 'React Component Build' }]}
        allTasks={catalogue}
        onAssignAssessmentTasks={onAssignAssessmentTasks}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: 'React Component Build' }));
    });
    expect(onAssignAssessmentTasks).toHaveBeenCalledWith([]);
  });

  it('manages a multi-task A/B set directly in Agent settings', async () => {
    const onAssignAssessmentTasks = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={catalogue}
        allTasks={catalogue}
        onAssignAssessmentTasks={onAssignAssessmentTasks}
      />,
    );
    expect(screen.getByText('2 tasks in A/B rotation')).toBeInTheDocument();
    expect(screen.getByText(/split evenly and stays stable/i)).toBeInTheDocument();
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);

    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: 'Async Debugging Challenge' }));
    });
    expect(onAssignAssessmentTasks).toHaveBeenCalledWith([701]);
  });

  it('adds search when the task library is long', () => {
    const longCatalogue = Array.from({ length: 7 }, (_, index) => ({
      id: 800 + index,
      name: `Assessment ${index + 1}`,
    }));
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        roleTasks={[]}
        allTasks={longCatalogue}
        onAssignAssessmentTasks={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search assessment tasks' }), {
      target: { value: 'Assessment 7' },
    });
    expect(screen.getByRole('checkbox', { name: 'Assessment 7' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Assessment 1' })).not.toBeInTheDocument();
  });
});

describe('RoleAgentSettingsTab budget validation', () => {
  it('renders agent configuration read-only without hiding the saved policy', () => {
    const reason = 'Only workspace owners, hiring managers, and recruiters assigned to this role can change its agent controls.';
    const onAutonomyChange = vi.fn();
    const onAssignAssessmentTasks = vi.fn();
    const onSave = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps()}
        canControlAgent={false}
        controlDisabledReason={reason}
        roleTasks={[{ id: 42, name: 'API exercise', is_active: true }]}
        allTasks={[{ id: 42, name: 'API exercise', is_active: true }]}
        onAutonomyChange={onAutonomyChange}
        onAssignAssessmentTasks={onAssignAssessmentTasks}
        onSave={onSave}
      />,
    );

    expect(screen.getByText('Agent settings are read-only')).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Auto-send assessments' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'API exercise' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Threshold mode' })).toBeDisabled();
    expect(screen.getByRole('slider', { name: 'Screening threshold percent' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save threshold' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeDisabled();
    expect(onAutonomyChange).not.toHaveBeenCalled();
    expect(onAssignAssessmentTasks).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });

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
