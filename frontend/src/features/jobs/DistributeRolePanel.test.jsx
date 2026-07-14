import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

const showToast = vi.fn();

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

vi.mock('../../shared/api', () => ({
  roles: {
    distribution: vi.fn(),
  },
}));

import { roles as rolesApi } from '../../shared/api';
import { DistributeRolePanel } from './DistributeRolePanel';

const publishedPayload = {
  published: true,
  distribution_ready: true,
  apply_url: 'https://app.example.com/job/abc123',
  title: 'Senior Backend Engineer',
  linkedin_post: "We're hiring: Senior Backend Engineer\n\nApply here: https://app.example.com/job/abc123",
  share_urls: {
    linkedin: 'https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fapp.example.com%2Fjob%2Fabc123',
    email: 'mailto:?subject=Job%20opportunity&body=x',
    apply_url: 'https://app.example.com/job/abc123',
  },
  feed_url: 'https://api.example.com/api/v1/public/careers/acme/feed.xml',
};

describe('DistributeRolePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  });

  const open = (props = {}) => {
    render(<DistributeRolePanel roleId={101} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: /Distribute this role/i }));
  };

  it('renders the artefacts for a published role, with copy + share controls', async () => {
    rolesApi.distribution.mockResolvedValue({ data: publishedPayload });
    open();

    // LinkedIn post populates an editable textarea.
    const textarea = await screen.findByLabelText('LinkedIn post draft');
    expect(textarea).toHaveValue(publishedPayload.linkedin_post);

    // Copy buttons present.
    expect(screen.getByRole('button', { name: /Copy post/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy apply link/i })).toBeInTheDocument();

    // Share links point at the share intents.
    const linkedin = screen.getByRole('link', { name: /Open in LinkedIn/i });
    expect(linkedin).toHaveAttribute('href', publishedPayload.share_urls.linkedin);
    const email = screen.getByRole('link', { name: /Email/i });
    expect(email).toHaveAttribute('href', publishedPayload.share_urls.email);

    // Feed URL shown for boards.
    expect(screen.getByText(publishedPayload.feed_url)).toBeInTheDocument();
  });

  it('copies the apply link', async () => {
    rolesApi.distribution.mockResolvedValue({ data: publishedPayload });
    open();
    const copyApply = await screen.findByRole('button', { name: /Copy apply link/i });
    fireEvent.click(copyApply);
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(publishedPayload.apply_url),
    );
  });

  it('shows a publish-first note for an unpublished role', async () => {
    rolesApi.distribution.mockResolvedValue({ data: { published: false } });
    open();
    expect(await screen.findByText(/Publish this role to distribute it/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('LinkedIn post draft')).not.toBeInTheDocument();
  });

  it('labels a published-but-off native page as preview-only and suppresses distribution artefacts', async () => {
    // The lifecycle-aware backend hides draft/off pages from feeds and returns
    // no distribution artefacts even though the recruiter can still preview.
    rolesApi.distribution.mockResolvedValue({ data: { published: true, distribution_ready: false, reason: 'agent_off' } });
    open({ jobStatus: 'draft' });

    expect(await screen.findByText(/Preview only.*not accepting applications/i)).toBeInTheDocument();
    expect(screen.getByText(/Do not distribute it yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/Publish this role to distribute it/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText('LinkedIn post draft')).not.toBeInTheDocument();
  });

  it('explains why a published role cannot be distributed while its agent is paused', async () => {
    rolesApi.distribution.mockResolvedValue({
      data: { published: true, distribution_ready: false, reason: 'agent_paused' },
    });
    open({ jobStatus: 'open' });

    expect(await screen.findByText(/Distribution is paused with the role/i)).toBeInTheDocument();
    expect(screen.getByText(/Resume the agent/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('LinkedIn post draft')).not.toBeInTheDocument();
  });

  it('fetches only after the panel is opened', async () => {
    rolesApi.distribution.mockResolvedValue({ data: publishedPayload });
    render(<DistributeRolePanel roleId={101} />);
    expect(rolesApi.distribution).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Distribute this role/i }));
    await waitFor(() => expect(rolesApi.distribution).toHaveBeenCalledWith(101));
  });

  it('shows a Retry affordance on failure and does NOT auto-retry', async () => {
    rolesApi.distribution.mockRejectedValueOnce(new Error('boom'));
    open();
    // Error state renders, not the publish note.
    expect(await screen.findByText(/Could not load distribution options/i)).toBeInTheDocument();
    expect(screen.queryByText(/Publish this role to distribute it/i)).not.toBeInTheDocument();
    // The failed fetch fired exactly once — no render-loop retry.
    await waitFor(() => expect(rolesApi.distribution).toHaveBeenCalledTimes(1));
    // Manual Retry re-fetches (and succeeds).
    rolesApi.distribution.mockResolvedValueOnce({ data: publishedPayload });
    fireEvent.click(screen.getByRole('button', { name: /Retry/i }));
    expect(await screen.findByLabelText('LinkedIn post draft')).toBeInTheDocument();
    expect(rolesApi.distribution).toHaveBeenCalledTimes(2);
  });
});
