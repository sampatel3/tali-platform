import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

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
