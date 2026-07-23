import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import DeckLinksPage from './DeckLinksPage';

const list = vi.fn();
const create = vi.fn();
const revoke = vi.fn();
const showToast = vi.fn();

vi.mock('../../shared/api', () => ({
  deckLinks: {
    list: (...args) => list(...args),
    create: (...args) => create(...args),
    revoke: (...args) => revoke(...args),
  },
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

const link = (over = {}) => ({
  id: 1,
  prospect_label: 'Venquis',
  note: null,
  url: 'https://www.taali.ai/deck/dck_abc',
  token: 'dck_abc',
  created_at: '2026-07-23T10:00:00Z',
  expires_at: null,
  revoked_at: null,
  is_revoked: false,
  view_count: 0,
  last_viewed_at: null,
  opens: [],
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue() } });
});

describe('DeckLinksPage', () => {
  it('shows each prospect link with its open state', async () => {
    list.mockResolvedValue({
      data: {
        links: [
          link(),
          link({ id: 2, prospect_label: 'Acme', view_count: 3, url: 'https://www.taali.ai/deck/dck_xyz' }),
        ],
      },
    });

    render(<DeckLinksPage />);

    expect(await screen.findByText('Venquis')).toBeInTheDocument();
    expect(screen.getByText('Not opened yet')).toBeInTheDocument();
    expect(screen.getByText('Opened 3×')).toBeInTheDocument();
    expect(screen.getByText('https://www.taali.ai/deck/dck_xyz')).toBeInTheDocument();
  });

  it('creates a link and copies it', async () => {
    list.mockResolvedValue({ data: { links: [] } });
    create.mockResolvedValue({ data: link({ prospect_label: 'Northwind' }) });

    render(<DeckLinksPage />);
    await screen.findByText(/No deck links yet/i);

    fireEvent.change(screen.getByPlaceholderText('Venquis'), {
      target: { value: 'Northwind' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create link/i }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({ prospect_label: 'Northwind', note: undefined }),
    );
    await waitFor(() =>
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        'https://www.taali.ai/deck/dck_abc',
      ),
    );
  });

  it('will not mint a link without a prospect', async () => {
    list.mockResolvedValue({ data: { links: [] } });
    render(<DeckLinksPage />);
    await screen.findByText(/No deck links yet/i);

    fireEvent.click(screen.getByRole('button', { name: /create link/i }));

    await waitFor(() => expect(showToast).toHaveBeenCalledWith('Who is this link for?', 'warning'));
    expect(create).not.toHaveBeenCalled();
  });

  it('revokes only after confirming, and marks just that row', async () => {
    list.mockResolvedValue({ data: { links: [link(), link({ id: 2, prospect_label: 'Acme' })] } });
    revoke.mockResolvedValue({ data: link({ is_revoked: true, revoked_at: '2026-07-23T12:00:00Z' }) });

    render(<DeckLinksPage />);
    await screen.findByText('Venquis');

    fireEvent.click(screen.getAllByRole('button', { name: /^revoke$/i })[0]);
    expect(revoke).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith(1));
    expect(await screen.findByText('Revoked')).toBeInTheDocument();
    // The other prospect's link is untouched — the whole point of per-link revocation.
    expect(screen.getByText('Acme')).toBeInTheDocument();
  });
});
