import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { DecisionRecorder } from './DecisionRecorder';

const baseProps = (overrides = {}) => ({
  decision: '',
  onDecisionChange: vi.fn(),
  rationale: '',
  onRationaleChange: vi.fn(),
  confidence: '',
  onConfidenceChange: vi.fn(),
  nextSteps: [],
  onToggleNextStep: vi.fn(),
  persisted: null,
  dirty: false,
  saving: false,
  savingMode: null,
  conflict: false,
  onReload: vi.fn(),
  onSaveDraft: vi.fn(),
  onSubmit: vi.fn(),
  ...overrides,
});

describe('DecisionRecorder', () => {
  it('hydrates the form and state header from a recorded decision', () => {
    render(
      <DecisionRecorder
        {...baseProps({
          decision: 'advance',
          rationale: 'Strong production judgment.',
          persisted: {
            status: 'submitted',
            version: 2,
            updatedBy: { id: 1, name: 'Sam Patel' },
            updatedAt: '2026-06-20T10:00:00Z',
            submittedAt: '2026-06-20T10:00:00Z',
            history: [],
          },
        })}
      />
    );

    // Recorded (submitted) state → "Update evaluation" primary affordance.
    expect(screen.getByRole('button', { name: 'Update evaluation' })).toBeInTheDocument();
    expect(screen.getByText('Recorded')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    // Attribution: who last recorded it.
    expect(screen.getByText('Sam Patel')).toBeInTheDocument();
    // The loaded decision is reflected as the selected option.
    expect(screen.getByText('Selected')).toBeInTheDocument();
  });

  it('shows a Submit affordance and no version when nothing is recorded yet', () => {
    render(<DecisionRecorder {...baseProps()} />);
    expect(screen.getByRole('button', { name: 'Submit evaluation' })).toBeInTheDocument();
    expect(screen.getByText('Not recorded')).toBeInTheDocument();
    expect(screen.queryByText(/^v\d+$/)).not.toBeInTheDocument();
  });

  it('routes Save draft and Submit to distinct handlers', () => {
    const onSaveDraft = vi.fn();
    const onSubmit = vi.fn();
    render(<DecisionRecorder {...baseProps({ dirty: true, onSaveDraft, onSubmit })} />);

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    expect(onSaveDraft).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Submit evaluation' }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('disables Submit on an unchanged recorded decision and enables it once dirty', () => {
    const persisted = { status: 'submitted', version: 1, updatedBy: { name: 'Sam' }, updatedAt: null, history: [] };
    const { rerender } = render(
      <DecisionRecorder {...baseProps({ persisted, dirty: false })} />
    );
    expect(screen.getByRole('button', { name: 'Update evaluation' })).toBeDisabled();

    rerender(<DecisionRecorder {...baseProps({ persisted, dirty: true })} />);
    expect(screen.getByRole('button', { name: 'Update evaluation' })).not.toBeDisabled();
  });

  it('disables Save draft until there are unsaved changes', () => {
    const { rerender } = render(<DecisionRecorder {...baseProps({ dirty: false })} />);
    expect(screen.getByRole('button', { name: 'Save draft' })).toBeDisabled();
    rerender(<DecisionRecorder {...baseProps({ dirty: true })} />);
    expect(screen.getByRole('button', { name: 'Save draft' })).not.toBeDisabled();
  });

  it('renders the change history on demand', () => {
    render(
      <DecisionRecorder
        {...baseProps({
          persisted: {
            status: 'submitted',
            version: 2,
            updatedBy: { name: 'Sam' },
            updatedAt: '2026-06-20T10:00:00Z',
            history: [
              { version: 1, action: 'saved_draft', status: 'draft', decision: 'hold', confidence: 'medium', rationale_excerpt: 'Leaning hold', at: '2026-06-19T09:00:00Z', by: { name: 'Sam' } },
              { version: 2, action: 'submitted', status: 'submitted', decision: 'advance', confidence: 'high', rationale_excerpt: 'Advancing', at: '2026-06-20T10:00:00Z', by: { name: 'Sam' } },
            ],
          },
        })}
      />
    );

    // Collapsed by default.
    expect(screen.queryByTestId('decision-history')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'History (2)' }));
    const history = screen.getByTestId('decision-history');
    expect(history).toBeInTheDocument();
    expect(screen.getByText('Saved draft')).toBeInTheDocument();
    expect(screen.getByText('Recorded decision')).toBeInTheDocument();
  });

  it('surfaces a conflict banner and a working Reload action', () => {
    const onReload = vi.fn();
    render(<DecisionRecorder {...baseProps({ conflict: true, onReload })} />);
    expect(screen.getByText(/updated elsewhere/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it('uses the entityNoun for the application (decision) variant', () => {
    render(<DecisionRecorder {...baseProps({ entityNoun: 'decision' })} />);
    expect(screen.getByRole('button', { name: 'Submit decision' })).toBeInTheDocument();
  });
});
