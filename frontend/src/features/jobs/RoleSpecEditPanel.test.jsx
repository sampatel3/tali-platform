import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { RoleSpecEditPanel } from './RoleSpecEditPanel';

const role = { id: 1, name: 'Senior AI Engineer', job_spec_text: 'The full spec text.', description: 'short blurb' };

describe('RoleSpecEditPanel (direct spec editor)', () => {
  it('seeds the editor from job_spec_text (not the short description)', () => {
    render(<RoleSpecEditPanel role={role} roleTasks={[]} allTasks={[]} onSubmit={vi.fn()} />);
    expect(screen.getByDisplayValue('The full spec text.')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Senior AI Engineer')).toBeInTheDocument();
    // No file-upload control on this surface.
    expect(screen.queryByText(/Choose a job specification file/i)).not.toBeInTheDocument();
  });

  it('submits the edited spec text as specText', () => {
    const onSubmit = vi.fn();
    render(<RoleSpecEditPanel role={role} roleTasks={[]} allTasks={[]} onSubmit={onSubmit} />);

    const specBox = screen.getByDisplayValue('The full spec text.');
    fireEvent.change(specBox, { target: { value: 'The updated spec.' } });
    fireEvent.click(screen.getByText('Save changes'));

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Senior AI Engineer',
      specText: 'The updated spec.',
    }));
  });

  it('Save is disabled until something changes; Cancel calls onCancel', () => {
    const onCancel = vi.fn();
    render(<RoleSpecEditPanel role={role} roleTasks={[]} allTasks={[]} onSubmit={vi.fn()} onCancel={onCancel} />);
    expect(screen.getByText('Save changes')).toBeDisabled();
    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancel).toHaveBeenCalled();
  });
});
