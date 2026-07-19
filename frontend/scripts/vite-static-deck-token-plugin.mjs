// Vite plugin that injects VITE_DEV_TOKEN into the static deck paths
// at build time. The deck HTML files live in ``public/_deck/`` so Vite
// copies them to ``dist/_deck/`` unchanged — by default there's no hook
// to substitute env vars. This plugin does a targeted
// ``__VITE_DEV_TOKEN__`` → env-value replacement in ``closeBundle``
// (after Vite has finished copying public/), so each static gate can
// compare against the same value TokenGate uses.
//
// Same model as TokenGate (the dev-route gate it mirrors): the
// secret is shipped to the browser anyway, so embedding it in
// either static deck file doesn't widen the trust surface.
//
// In dev (no build run) the placeholder stays as-is and the static
// paths bounce to their gated React routes instead.

import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const PLACEHOLDER = '__VITE_DEV_TOKEN__';
const DECK_OUTPUTS = ['index.html', 'hub71.html'];

// Build the U+2028 / U+2029 regexes via a single-char ``String``
// rather than typing the codepoints literally in source — esbuild
// treats raw U+2028/U+2029 as line terminators and the regex would
// fail to parse.
const LS = new RegExp(String.fromCharCode(0x2028), 'g');
const PS = new RegExp(String.fromCharCode(0x2029), 'g');

export default function staticDeckTokenPlugin() {
  return {
    name: 'static-deck-token',
    apply: 'build',
    async closeBundle() {
      const expected = (process.env.VITE_DEV_TOKEN || '').trim();
      // Inject as a JSON-encoded JS literal so tokens containing `'`,
      // `"`, `\`, or newlines can't break out of the string. Then
      // additionally HTML-escape ``<`` / ``>`` (so a token containing
      // ``</script>`` can't terminate the inline <script> block) and
      // the two paragraph/line separators (U+2028, U+2029) that count
      // as line terminators in legacy JS string contexts.
      const literal = JSON.stringify(expected)
        .replace(/</g, '\\u003C')
        .replace(/>/g, '\\u003E')
        .replace(LS, '\\u2028')
        .replace(PS, '\\u2029');

      await Promise.all(DECK_OUTPUTS.map(async (fileName) => {
        const outFile = resolve(process.cwd(), 'dist/_deck', fileName);
        let html;
        try {
          html = await readFile(outFile, 'utf8');
        } catch (err) {
          if (err.code === 'ENOENT') {
            // A deck variant is optional; leave builds without it alone.
            return;
          }
          throw err;
        }

        const next = html.split(PLACEHOLDER).join(literal);
        if (next !== html) {
          await writeFile(outFile, next, 'utf8');
        }
      }));
    },
  };
}
