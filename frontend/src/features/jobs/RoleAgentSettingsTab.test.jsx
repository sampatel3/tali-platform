import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

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

describe('RoleAgentSettingsTab auto-promote toggle', () => {
  it('surfaces the auto-promote toggle with plain auto-send + guard copy', () => {
    render(<RoleAgentSettingsTab {...baseProps()} />);
    expect(screen.getByText('Auto-promote')).toBeInTheDocument();
    // Discoverability: the copy must say the agent sends assessments
    // automatically to candidates who pass screening.
    expect(
      screen.getByText(/sends assessments automatically to candidates who pass screening/i),
    ).toBeInTheDocument();
    // And it must surface the safety guard (held for review at the daily/budget cap).
    expect(screen.getByText(/daily send limit or budget cap/i)).toBeInTheDocument();
  });

  it('reflects the persisted auto_promote value and fires onAutonomyChange', () => {
    const onAutonomyChange = vi.fn();
    render(<RoleAgentSettingsTab {...baseProps({ auto_promote: true })} onAutonomyChange={onAutonomyChange} />);
    const toggle = screen.getByRole('button', { name: 'Auto-promote' });
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(toggle);
    expect(onAutonomyChange).toHaveBeenCalledWith('auto_promote', false);
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
      screen.getByText(/nothing to send when a candidate passes screening/i),
    ).toBeInTheDocument();
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
