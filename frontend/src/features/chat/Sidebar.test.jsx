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
  it('uses the shared pressed-choice contract for Ask and Agents modes', () => {
    const onModeChange = vi.fn();
    render(
      <Sidebar
        mode="ask"
        onModeChange={onModeChange}
        conversations={[]}
        onNew={vi.fn()}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
        agents={[]}
        onSelectAgent={vi.fn()}
        agentAttention={3}
      />,
    );

    const group = screen.getByRole('group', { name: 'Chat mode' });
    expect(within(group).getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-pressed', 'true');
    const agents = within(group).getByRole('button', {
      name: 'Agents, 3 agent updates awaiting you',
    });
    expect(agents).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(agents);
    expect(onModeChange).toHaveBeenCalledWith('agents');
  });

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

  it('exposes mobile drawer semantics without changing the desktop sidebar contract', () => {
    const { rerender } = render(
      <Sidebar
        id="chat-navigation-drawer"
        mobileDrawer
        mobileDrawerOpen={false}
        onRequestClose={vi.fn()}
        mode="ask"
        onModeChange={vi.fn()}
        conversations={[]}
        onNew={vi.fn()}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const drawer = document.getElementById('chat-navigation-drawer');
    expect(drawer).toHaveAttribute('role', 'dialog');
    expect(drawer).toHaveAttribute('aria-hidden', 'true');
    expect(drawer).toHaveAttribute('inert');

    rerender(
      <Sidebar
        id="chat-navigation-drawer"
        mobileDrawer
        mobileDrawerOpen
        onRequestClose={vi.fn()}
        mode="ask"
        onModeChange={vi.fn()}
        conversations={[]}
        onNew={vi.fn()}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(drawer).not.toHaveAttribute('aria-hidden');
    expect(drawer).not.toHaveAttribute('inert');
    expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('button', { name: 'Close chat navigation' })).toBeInTheDocument();
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
