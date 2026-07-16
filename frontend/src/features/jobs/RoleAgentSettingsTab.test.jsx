import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('./RoleScreeningQuestions', () => ({
  default: () => <div data-testid="screening-question-editor" />,
}));

vi.mock('./RoleFeedbackNotes', () => ({
  default: () => <div data-testid="role-feedback-notes" />,
}));

vi.mock('./RecruiterAnswersLog', () => ({
  default: () => <div data-testid="recruiter-answers-log" />,
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

  it('previews the first-Turn-on autonomous default for an untouched role', async () => {
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
    expect(send).toHaveAttribute('aria-pressed', 'true');
    expect(resend).toHaveAttribute('aria-pressed', 'true');
    expect(advance).toHaveAttribute('aria-pressed', 'true');

    await act(async () => {
      fireEvent.click(send);
    });
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_send_assessment', false);
  });

  it('renders one consolidated deterministic-rejection control', async () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab
      {...baseProps({ auto_reject: true, auto_reject_pre_screen: false })}
      onAutonomyChange={onAutonomyChange}
    />);
    const toggle = screen.getByRole('button', { name: 'Auto-reject pre-screen failures' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: /^Auto-reject$/i })).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(onAutonomyChange).toHaveBeenCalledWith('deterministic_pre_screen_reject', false);
  });
});

describe('RoleAgentSettingsTab reject and pause boundaries', () => {
  it('makes the irreversible human-confirm rail explicit', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(
      screen.getAllByText(/full CV-score and assessment rejections/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getByText(/fail a required screening question or fall below the pre-screen threshold/i),
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
      screen.getByText(/Turn on will generate and validate a role-specific task automatically/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/nothing to send after screening/i)).toBeInTheDocument();
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
    const skipToggle = screen.getByRole('button', { name: 'Skip assessment for strong candidates' });
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

describe('RoleAgentSettingsTab related-role and large-roster boundaries', () => {
  it('keeps scoring and budget editable while related-role candidate actions stay read-only', async () => {
    const onAutonomyChange = vi.fn();
    const onAssignAssessmentTasks = vi.fn();
    const setThresholdDraft = vi.fn();
    const onSaveBudget = vi.fn();
    render(
      <RoleAgentSettingsTab
        {...baseProps({
          role_kind: 'sister',
          auto_send_assessment: true,
          auto_resend_assessment: true,
          auto_advance: true,
        })}
        setThresholdDraft={setThresholdDraft}
        roleTasks={[{ id: 42, name: 'Owner API exercise', is_active: true }]}
        allTasks={[
          { id: 42, name: 'Owner API exercise', is_active: true },
          { id: 43, name: 'Unrelated library task', is_active: true },
        ]}
        onAutonomyChange={onAutonomyChange}
        onAssignAssessmentTasks={onAssignAssessmentTasks}
        onSaveBudget={onSaveBudget}
      />,
    );

    expect(screen.getByText(/Related-role Agent is score-only/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Threshold mode' })).not.toBeDisabled();
    const threshold = screen.getByRole('slider', { name: 'Screening threshold percent' });
    expect(threshold).not.toBeDisabled();
    fireEvent.change(threshold, { target: { value: '61' } });
    expect(setThresholdDraft).toHaveBeenCalledWith('61');

    expect(screen.getByRole('checkbox', { name: 'Owner API exercise' })).toBeDisabled();
    expect(screen.queryByRole('checkbox', { name: 'Unrelated library task' })).not.toBeInTheDocument();
    for (const label of [
      'Auto-reject pre-screen failures',
      'Auto-send assessments',
      'Auto-retry assessment invites',
      'Auto-advance qualified candidates',
    ]) {
      const control = screen.getByRole('button', { name: label });
      expect(control).toBeDisabled();
      fireEvent.click(control);
    }
    expect(onAutonomyChange).not.toHaveBeenCalled();
    expect(onAssignAssessmentTasks).not.toHaveBeenCalled();

    const editBudget = screen.getByRole('button', { name: /edit/i });
    expect(editBudget).not.toBeDisabled();
    fireEvent.click(editBudget);
    expect(screen.getByRole('spinbutton', { name: 'Monthly budget in dollars' })).not.toBeDisabled();
  });

  it('preserves exact 10,000-candidate counts with at most 100 decorative dots', () => {
    const { container } = render(
      <RoleAgentSettingsTab
        {...baseProps()}
        activeApplications={Array.from({ length: 10_000 }, (_, id) => ({ id }))}
        belowThresholdCount={2_345}
      />,
    );

    expect(screen.getByText('PIPELINE DISTRIBUTION · 10000 SCORED')).toBeInTheDocument();
    expect(container.querySelector('.mc-agent-settings-distribution-summary'))
      .toHaveTextContent('2345 below threshold · 7655 above');
    expect(container.querySelectorAll('.mc-agent-settings-dot')).toHaveLength(100);
  });

  it.each([
    { belowThresholdCount: 0, expectedBelowDots: 0, expectedAboveDots: 100 },
    { belowThresholdCount: 1, expectedBelowDots: 1, expectedAboveDots: 99 },
    { belowThresholdCount: 999, expectedBelowDots: 99, expectedAboveDots: 1 },
    { belowThresholdCount: 1_000, expectedBelowDots: 100, expectedAboveDots: 0 },
  ])(
    'maps a 1,000-candidate cohort with $belowThresholdCount below without false endpoints',
    ({ belowThresholdCount, expectedBelowDots, expectedAboveDots }) => {
      const { container } = render(
        <RoleAgentSettingsTab
          {...baseProps()}
          activeApplications={Array.from({ length: 1_000 }, (_, id) => ({ id }))}
          belowThresholdCount={belowThresholdCount}
        />,
      );

      expect(container.querySelectorAll('.mc-agent-settings-dot')).toHaveLength(100);
      expect(container.querySelectorAll('.mc-agent-settings-dot.is-below'))
        .toHaveLength(expectedBelowDots);
      expect(container.querySelectorAll('.mc-agent-settings-dot.is-above'))
        .toHaveLength(expectedAboveDots);
      expect(container.querySelector('.mc-agent-settings-distribution-summary'))
        .toHaveTextContent(
          `${belowThresholdCount} below threshold · ${1_000 - belowThresholdCount} above`,
        );
    },
  );
});
