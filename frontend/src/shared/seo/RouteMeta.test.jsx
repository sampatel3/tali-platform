import { render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import TestMemoryRouter from '../../test/TestMemoryRouter';
import { RouteMeta } from './RouteMeta';

describe('RouteMeta', () => {
  beforeEach(() => {
    document.head.querySelectorAll('link[rel="canonical"], meta[name="robots"], meta[property="og:url"]')
      .forEach((element) => element.remove());
  });

  it('keeps each blog article self-canonical while inheriting indexable blog metadata', async () => {
    const path = '/blog/ai-native-coding-and-knowledge-work';
    render(
      <TestMemoryRouter initialEntries={[path]}>
        <RouteMeta />
      </TestMemoryRouter>,
    );

    await waitFor(() => {
      expect(document.head.querySelector('link[rel="canonical"]')).toHaveAttribute(
        'href',
        `https://www.taali.ai${path}`,
      );
    });
    expect(document.head.querySelector('meta[property="og:url"]')).toHaveAttribute(
      'content',
      `https://www.taali.ai${path}`,
    );
    expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute(
      'content',
      expect.stringContaining('index'),
    );
  });
});
