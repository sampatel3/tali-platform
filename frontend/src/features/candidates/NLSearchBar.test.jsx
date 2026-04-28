import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { NLSearchBar } from './NLSearchBar';

const NOOP = () => undefined;

function renderBar(overrides = {}) {
  const props = {
    nlQuery: '',
    onSubmit: vi.fn(),
    onClear: vi.fn(),
    parsedFilter: null,
    onRemoveChip: vi.fn(),
    warnings: [],
    viewMode: 'list',
    onViewModeChange: vi.fn(),
    isLoading: false,
    ...overrides,
  };
  return { ...render(<NLSearchBar {...props} />), props };
}

describe('NLSearchBar', () => {
  it('submits the typed query', () => {
    const { props } = renderBar();
    fireEvent.change(screen.getByLabelText(/natural-language candidate search/i), {
      target: { value: 'AWS Glue' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(props.onSubmit).toHaveBeenCalledWith('AWS Glue');
  });

  it('renders chips from parsed_filter and removes one when X clicked', () => {
    const { props } = renderBar({
      nlQuery: 'AWS Glue, UK',
      parsedFilter: {
        skills_all: ['AWS Glue'],
        locations_country: ['United Kingdom'],
      },
    });
    expect(screen.getByText(/Skill: AWS Glue/i)).toBeInTheDocument();
    expect(screen.getByText(/Country: United Kingdom/i)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Remove Skill: AWS Glue'));
    expect(props.onRemoveChip).toHaveBeenCalledWith('skills_all', 'AWS Glue');
  });

  it('shows the year chip', () => {
    renderBar({
      nlQuery: '5 years',
      parsedFilter: { min_years_experience: 5 },
    });
    expect(screen.getByText('5+ years')).toBeInTheDocument();
  });

  it('shows graph predicate chips with the right verb', () => {
    renderBar({
      nlQuery: 'worked at Google',
      parsedFilter: {
        graph_predicates: [{ type: 'worked_at', value: 'Google' }],
      },
    });
    expect(screen.getByText(/Worked at: Google/i)).toBeInTheDocument();
  });

  it('shows the global Clear all when chips exist', () => {
    const { props } = renderBar({
      nlQuery: 'AWS Glue',
      parsedFilter: { skills_all: ['AWS Glue'] },
    });
    fireEvent.click(screen.getByRole('button', { name: /clear all/i }));
    expect(props.onClear).toHaveBeenCalled();
  });

  it('renders example queries on focus when query is empty', () => {
    renderBar();
    fireEvent.focus(screen.getByLabelText(/natural-language candidate search/i));
    expect(screen.getByText(/AWS Glue experience, based in UK/i)).toBeInTheDocument();
  });

  it('toggles view mode', () => {
    const { props } = renderBar({ viewMode: 'list' });
    fireEvent.click(screen.getByRole('button', { name: /Graph/i, pressed: false }));
    expect(props.onViewModeChange).toHaveBeenCalledWith('graph');
  });

  it('surfaces warnings beneath the input', () => {
    renderBar({
      warnings: [{ code: 'neo4j_unavailable', message: 'Graph features unavailable' }],
    });
    expect(screen.getByText(/Graph features unavailable/i)).toBeInTheDocument();
  });

  it('does not submit empty query', () => {
    const { props } = renderBar();
    fireEvent.click(screen.getByRole('button', { name: 'Search' }));
    expect(props.onSubmit).not.toHaveBeenCalled();
  });
});
