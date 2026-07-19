/**
 * DeckIframe — full-viewport iframe loader for the static decks in
 * `frontend/public/_deck/` so it can keep the 1920×1080 stage scaling and
 * `window.print()`-as-PDF behaviour without fighting the React shell.
 */

import { useEffect } from 'react';

const DECK_VARIANTS = {
  default: {
    title: 'Taali Deck',
    src: '/_deck/index.html',
  },
  hub71: {
    title: 'Taali Hub71 Access Deck',
    src: '/_deck/hub71.html',
  },
};

export default function DeckIframe({ variant = 'default' }) {
  const deck = DECK_VARIANTS[variant] || DECK_VARIANTS.default;
  const searchParams = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search)
    : new URLSearchParams();
  const exportMode = searchParams.get('export') === '1';
  const slide = Number.parseInt(searchParams.get('slide') || '', 10);
  const slideHash = Number.isInteger(slide) && slide > 0 ? `#s=${slide}` : '';
  const src = `${deck.src}${exportMode ? '?export=1' : ''}${slideHash}`;

  useEffect(() => {
    // Hide whatever app chrome / scrollbars the shell would normally add.
    const prevHtmlOverflow = document.documentElement.style.overflow;
    const prevBodyOverflow = document.body.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    return () => {
      document.documentElement.style.overflow = prevHtmlOverflow;
      document.body.style.overflow = prevBodyOverflow;
    };
  }, []);

  return (
    <iframe
      title={deck.title}
      src={src}
      style={{
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        border: 0,
        background: 'var(--taali-deck-backdrop)',
      }}
      // Full permissions because it's our own static asset.
      allow="fullscreen"
    />
  );
}
