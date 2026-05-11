// Vite plugin that injects VITE_DEV_TOKEN into the static deck path
// at build time. The deck's index.html lives in ``public/_deck/`` so
// Vite copies it to ``dist/_deck/`` unchanged — by default there's no
// hook to substitute env vars. This plugin does one targeted
// ``__VITE_DEV_TOKEN__`` → env-value replacement in ``closeBundle``
// (after Vite has finished copying public/), so the static gate can
// compare against the same value TokenGate uses.
//
// Same model as TokenGate (the dev-route gate it mirrors): the
// secret is shipped to the browser anyway, so embedding it in
// dist/_deck/index.html doesn't widen the trust surface.
//
// In dev (no build run) the placeholder stays as-is and the static
// path bounces to /deck — devs access the deck through that route.

import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const PLACEHOLDER = '__VITE_DEV_TOKEN__';

export default function staticDeckTokenPlugin() {
  return {
    name: 'static-deck-token',
    apply: 'build',
    async closeBundle() {
      const outFile = resolve(process.cwd(), 'dist/_deck/index.html');
      let html;
      try {
        html = await readFile(outFile, 'utf8');
      } catch (err) {
        if (err.code === 'ENOENT') {
          // No deck in this build (e.g., a future variant might drop it) —
          // nothing to do.
          return;
        }
        throw err;
      }
      const expected = (process.env.VITE_DEV_TOKEN || '').trim();
      // Inject as a JSON-encoded JS literal so tokens containing `'`,
      // `"`, `\`, or newlines can't break out of the string and
      // invalidate the script. ``JSON.stringify`` produces e.g.
      // ``"a\"b"`` for input ``a"b``; the placeholder is a bare
      // identifier (no surrounding quotes), so the replacement becomes
      // ``var expected = "a\"b";`` cleanly. An empty env collapses to
      // ``""`` which the static gate's ``!expected`` branch treats as
      // fail-closed.
      const literal = JSON.stringify(expected);
      const next = html.split(PLACEHOLDER).join(literal);
      if (next !== html) {
        await writeFile(outFile, next, 'utf8');
      }
    },
  };
}
