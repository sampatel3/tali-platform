import { render, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';

import { RouteMeta } from './RouteMeta';

const renderAt = (path) => render(
  <MemoryRouter initialEntries={[path]}>
    <RouteMeta />
  </MemoryRouter>,
);

describe('RouteMeta private route handling', () => {
  beforeEach(() => {
    document.head.querySelectorAll('meta[name="robots"], meta[property="og:url"], link[rel="canonical"]')
      .forEach((element) => element.remove());
  });

  it('never repeats an assessment invite token in canonical metadata', async () => {
    renderAt('/assess/invite-secret-value');

    await waitFor(() => {
      expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute(
        'content',
        'noindex, nofollow, noarchive, nosnippet',
      );
    });
    expect(document.head.querySelector('link[rel="canonical"]')).toHaveAttribute(
      'href',
      'https://www.taali.ai/',
    );
    expect(document.head.querySelector('meta[property="og:url"]')).toHaveAttribute(
      'content',
      'https://www.taali.ai/',
    );
    expect(document.head.innerHTML).not.toContain('invite-secret-value');
  });
});
