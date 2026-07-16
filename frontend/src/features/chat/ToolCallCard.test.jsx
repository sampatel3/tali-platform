import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ToolCallCard from './ToolCallCard';


describe('ToolCallCard disclosure', () => {
  it('exposes its collapsed and expanded state to assistive technology', () => {
    render(
      <ToolCallCard
        part={{
          toolName: 'get_recruiting_overview',
          args: {},
          result: { assessments: { needs_attention: 2 } },
          status: 'complete',
        }}
      />,
    );

    const disclosure = screen.getByRole('button', { name: /Checking recruiting operations/i });
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(disclosure);

    expect(disclosure).toHaveAttribute('aria-expanded', 'true');
  });

  it('uses the shared error activity language when a tool fails', () => {
    render(
      <ToolCallCard
        part={{
          toolName: 'find_top_candidates',
          args: { limit: 5 },
          status: 'error',
        }}
      />,
    );

    const activity = screen.getByRole('article', {
      name: 'Error tool activity: Ranking top candidates',
    });
    expect(activity).toHaveAttribute('data-severity', 'error');
    expect(screen.getByText('The tool did not complete.')).toBeInTheDocument();
  });
});
