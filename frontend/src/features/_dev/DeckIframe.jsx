/**
 * DeckIframe — full-viewport iframe loader for the static deck at
 * `/_deck/index.html`. The deck is a self-contained HTML/CSS/JS bundle in
 * `frontend/public/_deck/` so it can keep the 1920×1080 stage scaling and
 * `window.print()`-as-PDF behaviour without fighting the React shell.
 */

import { useEffect } from 'react';

export default function DeckIframe() {
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
      title="Taali Deck"
      src="/_deck/index.html"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100vw',
        height: '100vh',
        border: 0,
        background: '#0d0a14',
      }}
      // Full permissions because it's our own static asset.
      allow="fullscreen"
    />
  );
}
