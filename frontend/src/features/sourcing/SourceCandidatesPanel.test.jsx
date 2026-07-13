import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const showToast = vi.fn();

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('../../shared/api', () => ({
  roles: {
    sourcingSearches: vi.fn(),
    outreachDraft: vi.fn(),
  },
}));

import { roles as rolesApi } from '../../shared/api';
import { SourceCandidatesPanel } from './SourceCandidatesPanel';

const searchPayload = {
  deterministic: {
    xray: 'site:linkedin.com/in "Senior Data Engineer" "Apache Spark"',
    boolean: '"Senior Data Engineer" AND "Apache Spark"',
  },
  refined: [
    { label: 'Broader', xray: 'site:linkedin.com/in "Data Engineer"', boolean: '"Data Engineer"' },
  ],
  title_synonyms: ['Analytics Engineer'],
};

describe('SourceCandidatesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
  });

  const open = () => {
    render(<SourceCandidatesPanel roleId={101} />);
    fireEvent.click(screen.getByRole('button', { name: /Source candidates/i }));
  };

  it('renders the deterministic + refined strings and copies one', async () => {
    rolesApi.sourcingSearches.mockResolvedValue({ data: searchPayload });
    open();

    fireEvent.click(screen.getByRole('button', { name: /Generate search strings/i }));

    await waitFor(() => {
      expect(
        screen.getByText('site:linkedin.com/in "Senior Data Engineer" "Apache Spark"'),
      ).toBeInTheDocument();
    });
    // Boolean + refined alternate + synonyms all render.
    expect(screen.getByText('"Senior Data Engineer" AND "Apache Spark"')).toBeInTheDocument();
    expect(screen.getByText('Broader')).toBeInTheDocument();
    expect(screen.getByText('Analytics Engineer')).toBeInTheDocument();

    // Copy the first string.
    fireEvent.click(screen.getAllByRole('button', { name: /^Copy$/i })[0]);
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        'site:linkedin.com/in "Senior Data Engineer" "Apache Spark"',
      );
    });
  });

  it('shows the fail-open warning from the search response', async () => {
    rolesApi.sourcingSearches.mockResolvedValue({
      data: {
        deterministic: { xray: 'site:linkedin.com/in "Eng"', boolean: '"Eng"' },
        refined: [],
        title_synonyms: [],
        warning: "Couldn't generate refined suggestions — showing the base search strings.",
      },
    });
    open();
    fireEvent.click(screen.getByRole('button', { name: /Generate search strings/i }));

    await waitFor(() => {
      expect(screen.getByText(/Couldn't generate refined suggestions/i)).toBeInTheDocument();
    });
    expect(screen.getByText('"Eng"')).toBeInTheDocument();
  });

  it('drafts outreach and surfaces no-fabrication warnings', async () => {
    rolesApi.outreachDraft.mockResolvedValue({
      data: {
        subject: null,
        body: 'Hi — your Spark work stood out.',
        warnings: ['Profile is thin — add more detail for a stronger message.'],
      },
    });
    open();

    fireEvent.change(
      screen.getByPlaceholderText(/Paste a candidate's LinkedIn profile/i),
      { target: { value: 'Spark engineer, 5 years.' } },
    );
    fireEvent.click(screen.getByRole('button', { name: /^Draft outreach$/i }));

    await waitFor(() => {
      expect(screen.getByText('Hi — your Spark work stood out.')).toBeInTheDocument();
    });
    expect(screen.getByText(/Profile is thin/i)).toBeInTheDocument();
    expect(rolesApi.outreachDraft).toHaveBeenCalledWith(101, {
      profile_text: 'Spark engineer, 5 years.',
      tone: 'warm',
      channel: 'linkedin',
    });
  });

  it('warns when drafting with an empty profile (no API call)', async () => {
    open();
    fireEvent.click(screen.getByRole('button', { name: /^Draft outreach$/i }));
    expect(rolesApi.outreachDraft).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith('Paste a profile first.', 'error');
  });

  it('hands pasted profile context back to the sourcing workflow', async () => {
    const onPrepareProspect = vi.fn();
    render(
      <SourceCandidatesPanel
        roleId={101}
        defaultOpen
        onPrepareProspect={onPrepareProspect}
      />,
    );

    const saveButton = screen.getByRole('button', { name: /continue to save prospect/i });
    expect(saveButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Candidate profile or CV text'), {
      target: { value: 'Staff Spark engineer in Dubai.' },
    });
    fireEvent.click(saveButton);

    expect(onPrepareProspect).toHaveBeenCalledWith({
      profileText: 'Staff Spark engineer in Dubai.',
      draft: null,
      roleId: 101,
    });
  });
});
