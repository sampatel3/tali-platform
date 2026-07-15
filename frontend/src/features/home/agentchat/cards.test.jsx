import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ImpactCard, NeedsInputCard } from './cards';


describe('Agent Chat operation cards', () => {
  it('renders a proactive helper prompt whose quick replies only return composer text', () => {
    const prompts = [];
    render(
      <ImpactCard
        card={{
          type: 'helper_prompt',
          title: 'The review queue is growing',
          summary: 'Five candidates are ready for a closer look.',
          question: 'Would you like a concise comparison?',
          priority: 'helpful',
          suggestions: [
            { label: 'Compare them', prompt: 'Compare the five candidates waiting for review.' },
          ],
        }}
        onPrompt={(prompt) => prompts.push(prompt)}
      />,
    );

    expect(screen.getByTestId('helper-prompt')).toHaveTextContent('The review queue is growing');
    expect(screen.getByText('Five candidates are ready for a closer look.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Compare them' }));
    expect(prompts).toEqual(['Compare the five candidates waiting for review.']);
  });

  it('renders a durable event with accessible severity, details, source, and editable follow-up', () => {
    const prompts = [];
    render(
      <ImpactCard
        card={{
          type: 'agent_event',
          event_type: 'run_failed',
          severity: 'error',
          title: 'The scheduled review did not finish',
          summary: 'No candidate state changed. The provider timed out.',
          details: [
            { label: 'Attempt', value: 3 },
            { label: 'Reason', value: 'Provider timeout' },
            { label: '', value: 'Dropped malformed detail' },
          ],
          source: {
            type: 'agent_run',
            id: 'run-42',
            label: 'Scheduled run 42',
            href: '/settings/background-jobs?run=run-42',
          },
          occurred_at: '2026-07-15T08:30:00Z',
          suggestions: [{ label: 'Investigate', prompt: 'Investigate why the scheduled review failed.' }],
        }}
        onPrompt={(prompt) => prompts.push(prompt)}
      />,
    );

    expect(screen.getByRole('article', {
      name: 'Error agent event: The scheduled review did not finish',
    })).toHaveAttribute('data-severity', 'error');
    expect(screen.getByText('Error')).toBeInTheDocument();
    expect(screen.getByText('Run failed')).toBeInTheDocument();
    expect(screen.getByText('Provider timeout')).toBeInTheDocument();
    expect(screen.queryByText('Dropped malformed detail')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open Scheduled run 42' })).toHaveAttribute(
      'href',
      '/settings/background-jobs?run=run-42',
    );
    expect(screen.getByText('Details').closest('details')).not.toHaveAttribute('open');

    fireEvent.click(screen.getByRole('button', { name: /Investigate/ }));
    expect(prompts).toEqual(['Investigate why the scheduled review failed.']);
  });

  it.each([
    ['info', 'Info'],
    ['success', 'Completed'],
    ['warning', 'Warning'],
    ['not-a-severity', 'Info'],
  ])('renders %s events with the visible %s severity label', (severity, label) => {
    render(
      <ImpactCard
        card={{
          type: 'agent_event',
          event_type: 'agent_update',
          severity,
          title: `${severity} update`,
          summary: 'A durable update.',
          source: { type: 'agent_run', id: 5, href: 'https://example.com/run/5' },
        }}
      />,
    );

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByTestId('agent-event')).toHaveAttribute(
      'data-severity',
      severity === 'not-a-severity' ? 'info' : severity,
    );
    expect(screen.queryByRole('link', { name: /Agent run #5/ })).not.toBeInTheDocument();
  });

  it('renders an application-operation preview as unexecuted', () => {
    render(
      <ImpactCard
        card={{
          type: 'operation_preview',
          operation: 'post_workable_note',
          preview: {
            candidate: 'Ada Lovelace',
            application_id: 42,
            body_preview: 'Please review the salary context.',
          },
        }}
      />,
    );

    expect(screen.getByTestId('operation-preview')).toHaveTextContent('Confirmation required');
    expect(screen.getByText('Post Workable note')).toBeInTheDocument();
    expect(screen.getByText('Ada Lovelace')).toBeInTheDocument();
    expect(screen.getByText(/No action has run/)).toBeInTheDocument();
  });

  it('renders decision previews and committed operation receipts distinctly', () => {
    const { rerender } = render(
      <ImpactCard
        card={{
          type: 'decision_action_preview',
          operation: 'approve_decision',
          decision: {
            decision_id: 7,
            candidate_name: 'Grace Hopper',
            decision_type: 'send_assessment',
          },
          requested_action: {},
        }}
      />,
    );
    expect(screen.getByTestId('decision-action-preview')).toHaveTextContent('Grace Hopper');
    expect(screen.getByText(/No action has run/)).toBeInTheDocument();

    rerender(
      <ImpactCard
        card={{
          type: 'operation_receipt',
          status: 'queued',
          message: 'Decision 7 was accepted for processing.',
        }}
      />,
    );
    expect(screen.getByTestId('operation-receipt')).toHaveTextContent('queued');
    expect(screen.getByText('Decision 7 was accepted for processing.')).toBeInTheDocument();
  });
});

describe('Agent Chat recruiter questions', () => {
  it('submits typed numeric answers through the same timeline card', async () => {
    const answers = [];
    render(
      <NeedsInputCard
        item={{
          status: 'open',
          needs_input_id: 17,
          prompt: 'What score threshold should I use?',
          rationale: 'I need this before I can triage the current pool consistently.',
          input_mode: 'integer',
          can_answer: true,
          can_dismiss: false,
          response_schema: { type: 'integer', minimum: 0, maximum: 100 },
        }}
        onAnswer={async (...args) => answers.push(args)}
      />,
    );

    fireEvent.change(screen.getByLabelText('Answer the agent'), { target: { value: '72' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
    });

    expect(answers).toEqual([[17, { value: 72 }]]);
    expect(screen.getByText(/triage the current pool consistently/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dismiss' })).not.toBeInTheDocument();
  });

  it('renders external resolution links without inventing a text answer', () => {
    render(
      <NeedsInputCard
        item={{
          status: 'open',
          needs_input_id: 18,
          prompt: 'Add the missing job specification.',
          input_mode: 'external',
          can_answer: false,
          response_schema: { link_url: '/jobs/4', link_label: 'Open role' },
        }}
      />,
    );

    expect(screen.getByRole('link', { name: /Open role/ })).toHaveAttribute('href', '/jobs/4');
    expect(screen.queryByLabelText('Answer the agent')).not.toBeInTheDocument();
  });

  it('renders an unreadable-CV request as an actionable, semantic prompt', () => {
    const prompts = [];
    const answers = [];
    render(
      <NeedsInputCard
        item={{
          status: 'open',
          needs_input_id: 19,
          question_kind: 'cv_unreadable',
          prompt: 'Four candidates have a CV that could not be read.',
          input_mode: 'external',
          can_answer: false,
          can_dismiss: true,
        }}
        onAnswer={(...args) => answers.push(args)}
        onPrompt={(prompt) => prompts.push(prompt)}
      />,
    );

    expect(screen.getByRole('article', { name: 'CVs need readable text' })).toHaveAttribute(
      'data-status',
      'open',
    );
    expect(screen.getByText('Needs your input')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Skip for now' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Review affected candidates' }));

    expect(prompts).toEqual([
      'Show me the candidates whose CVs could not be read and what I can do for each.',
    ]);
    expect(answers).toEqual([]);
  });

  it('guards the quiet dismiss action while its request is pending', async () => {
    const dismissals = [];
    let finishDismiss;
    render(
      <NeedsInputCard
        item={{
          status: 'open',
          needs_input_id: 20,
          question_kind: 'candidate_tie_break',
          prompt: 'Which candidate should I prioritise?',
          options: [{ value: 'marcus', label: 'Marcus' }],
          can_dismiss: true,
        }}
        onDismiss={(id) => {
          dismissals.push(id);
          return new Promise((resolve) => {
            finishDismiss = resolve;
          });
        }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));

    expect(screen.getByRole('button', { name: 'Skipping request' })).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('article')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: 'Marcus' })).toBeDisabled();
    expect(dismissals).toEqual([20]);

    await act(async () => {
      finishDismiss();
    });

    expect(screen.getByRole('button', { name: 'Skip for now' })).not.toBeDisabled();
  });

  it('preserves a typed answer when its parent reports a save failure', async () => {
    render(
      <NeedsInputCard
        item={{
          status: 'open',
          needs_input_id: 22,
          prompt: 'What score threshold should I use?',
          input_mode: 'integer',
          can_answer: true,
          can_dismiss: false,
          response_schema: { type: 'integer', minimum: 0, maximum: 100 },
        }}
        onAnswer={async () => false}
      />,
    );

    const input = screen.getByLabelText('Answer the agent');
    fireEvent.change(input, { target: { value: '72' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Answer' }));
    });

    expect(input).toHaveValue(72);
  });

  it.each([
    'https://attacker.example/jobs/4',
    '//attacker.example/jobs/4',
    'javascript:alert(1)',
    '/jobs\\attacker.example',
  ])('does not render an unsafe agent-provided resolution link: %s', (linkUrl) => {
    render(
      <NeedsInputCard
        item={{
          status: 'open',
          needs_input_id: 21,
          prompt: 'Open this route to resolve the request.',
          input_mode: 'external',
          can_answer: false,
          can_dismiss: false,
          response_schema: { link_url: linkUrl, link_label: 'Open role' },
        }}
      />,
    );

    expect(screen.queryByRole('link', { name: /Open role/ })).not.toBeInTheDocument();
  });
});

describe('related-role chat cards', () => {
  it.each([
    ['workable', 'Workable'],
    ['bullhorn', 'Bullhorn'],
  ])('names %s as the owning candidate-pool provider', (provider, label) => {
    render(
      <ImpactCard
        card={{
          type: 'related_role_preview',
          ats_provider: provider,
          proposed_name: 'Platform Engineer · Related',
          candidates_total: 4,
          candidates_with_cv: 3,
          candidates_missing_cv: 1,
        }}
      />,
    );

    expect(screen.getByText(new RegExp(`original ${label} role`, 'i'))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`coupled to the original ${label} job`, 'i')))
      .toBeInTheDocument();
  });
});
