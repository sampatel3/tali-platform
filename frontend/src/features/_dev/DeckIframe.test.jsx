import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import DeckIframe from './DeckIframe';

afterEach(() => {
  window.history.replaceState({}, '', '/');
});

describe('DeckIframe', () => {
  it('keeps the established deck as the default', () => {
    render(<DeckIframe />);

    expect(screen.getByTitle('Taali Deck'))
      .toHaveAttribute('src', '/_deck/index.html');
  });

  it('loads the Hub71 deck only for the explicit variant', () => {
    render(<DeckIframe variant="hub71" />);

    expect(screen.getByTitle('Taali Hub71 Access Deck'))
      .toHaveAttribute('src', '/_deck/hub71.html');
  });

  it('forwards capture mode and a validated slide number to the static deck', () => {
    window.history.replaceState({}, '', '/deck/hub71?export=1&slide=4');

    render(<DeckIframe variant="hub71" />);

    expect(screen.getByTitle('Taali Hub71 Access Deck'))
      .toHaveAttribute('src', '/_deck/hub71.html?export=1#s=4');
  });
});
