import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';

import Sidebar from './Sidebar';

const LONG_TITLE = 'Platform engineers with Azure Foundry and GenAI production experience';

const renderSidebar = () => {
  const onDelete = vi.fn();
  const onSelect = vi.fn();

  render(
    <Sidebar
      mode="ask"
      onModeChange={vi.fn()}
      conversations={[{
        id: 7,
        title: LONG_TITLE,
        message_count: 10,
        updated_at: new Date().toISOString(),
      }]}
      activeId={7}
      onNew={vi.fn()}
      onSelect={onSelect}
      onDelete={onDelete}
      agents={[]}
      activeRoleId={null}
      onSelectAgent={vi.fn()}
    />,
  );

  return { onDelete, onSelect };
};

describe('Chat Sidebar conversation actions', () => {
  it('keeps the delete action separate from a long conversation title', () => {
    const { onDelete, onSelect } = renderSidebar();
    const conversation = screen.getByText(LONG_TITLE).closest('button');
    const deleteButton = screen.getByRole('button', { name: 'Delete conversation' });

    expect(conversation).toHaveClass('cp-conv');
    expect(conversation?.parentElement).toHaveClass('cp-conv-row');
    expect(deleteButton).toHaveClass('cp-conv-del', 'taali-icon-btn-sm');
    expect(conversation).not.toContainElement(deleteButton);

    fireEvent.click(deleteButton);
    expect(onDelete).toHaveBeenCalledWith(7);
    expect(onSelect).not.toHaveBeenCalled();
  });
});

describe('Chat Sidebar agent workspace hold', () => {
  it('shows the workspace actor and does not animate a held agent as ON', () => {
    render(
      <Sidebar
        mode="agents"
        onModeChange={vi.fn()}
        conversations={[]}
        activeId={null}
        onNew={vi.fn()}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        agents={[{
          role_id: 41,
          role_name: 'Platform Engineer',
          group: 'on_paused',
          agent_enabled: true,
          agent_effective_paused: true,
          agent_paused: true,
          agent_pause_scope: 'workspace',
          role_paused: false,
          workspace_paused: true,
          workspace_paused_by: { name: 'Jade Smith', is_current_user: false },
        }]}
        activeRoleId={null}
        onSelectAgent={vi.fn()}
      />,
    );

    const row = screen.getByText('Platform Engineer').closest('button');
    expect(row).toHaveAttribute('data-agent-state', 'held');
    expect(within(row).getByText('Held · Workspace paused by Jade Smith')).toBeInTheDocument();
    expect(row.querySelector('.cp-agent-stat-on')).toBeNull();
    expect(row.querySelector('.cp-agent-stat-paused')).not.toBeNull();
  });
});
