import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { RoleSpecEditPanel } from './RoleSpecEditPanel';

const SPEC = [
  '## About the role',
  'Build reliable data products for teams across the business.',
  '',
  '## Requirements',
  '- Strong AWS Glue and Python experience',
  '- Clear written communication',
].join('\n');

const role = {
  id: 26,
  name: 'AWS Glue Data Engineer',
  source: 'workable',
  description: 'Legacy description must not seed the editor.',
  job_spec_text: SPEC,
};

const renderEditor = (props = {}) => render(
  <RoleSpecEditPanel
    role={role}
    onSubmit={vi.fn()}
    onCancel={vi.fn()}
    {...props}
  />,
);

describe('RoleSpecEditPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('edits the authoritative job spec and explains Workable ownership', () => {
    renderEditor();

    expect(screen.getByLabelText('Job description')).toHaveValue(SPEC);
    expect(screen.getByText(/Workable source connected/i)).toBeInTheDocument();
    expect(screen.getByText(/future Workable syncs will not overwrite it/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Role title, managed in Workable/i)).toHaveTextContent('AWS Glue Data Engineer');
    expect(screen.queryByDisplayValue(/Legacy description/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save job spec/i })).toBeDisabled();
  });

  it('submits a trimmed authoritative spec and protects externally managed titles', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    renderEditor({ onSubmit });

    const updated = `${SPEC}\n\n## Benefits\n- Flexible hybrid working`;
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: `  ${updated}  ` } });
    fireEvent.click(screen.getByRole('button', { name: /Save job spec/i }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({ name: undefined, jobSpecText: updated });
    });
  });

  it('validates short descriptions with useful guidance', () => {
    renderEditor();

    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: 'Too short' } });
    fireEvent.blur(screen.getByLabelText('Job description'));

    expect(screen.getByRole('alert')).toHaveTextContent(/Add at least 51 more characters/i);
    expect(screen.getByRole('button', { name: /Save job spec/i })).toBeDisabled();
  });

  it('shows a formatted preview without losing the draft', () => {
    renderEditor();

    fireEvent.click(screen.getByRole('tab', { name: /Preview/i }));
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Requirements');
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Strong AWS Glue and Python experience');

    fireEvent.click(screen.getByRole('tab', { name: /Write/i }));
    expect(screen.getByLabelText('Job description')).toHaveValue(SPEC);
  });

  it('asks before discarding a dirty draft', () => {
    const onCancel = vi.fn();
    renderEditor({ onCancel });

    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: `${SPEC}\nOne more detail.` } });
    fireEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    expect(onCancel).not.toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: /Discard your changes/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Discard changes/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('keeps a dirty draft through same-role refreshes and resets for another role', () => {
    const { rerender } = renderEditor();
    const draft = `${SPEC}\nRecruiter-authored draft.`;
    fireEvent.change(screen.getByLabelText('Job description'), { target: { value: draft } });

    rerender(
      <RoleSpecEditPanel
        role={{ ...role, job_spec_text: `${SPEC}\nBackground refresh.` }}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Job description')).toHaveValue(draft);

    rerender(
      <RoleSpecEditPanel
        role={{ ...role, id: 27, name: 'Platform Engineer', job_spec_text: `${SPEC}\nDifferent role.` }}
        onSubmit={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Job description')).toHaveValue(`${SPEC}\nDifferent role.`);
  });

  it('allows a Taali-owned role title to be edited', async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    renderEditor({ role: { ...role, source: 'taali' }, onSubmit });

    fireEvent.change(screen.getByLabelText('Role title'), { target: { value: '  Principal Data Engineer  ' } });
    fireEvent.click(screen.getByRole('button', { name: /Save job spec/i }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        name: 'Principal Data Engineer',
        jobSpecText: SPEC,
      });
    });
  });
});
