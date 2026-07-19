import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import staticDeckTokenPlugin from './vite-static-deck-token-plugin.mjs';

const fixtureRoots = [];
const originalToken = process.env.VITE_DEV_TOKEN;

async function makeBuild(files) {
  const root = await mkdtemp(join(tmpdir(), 'taali-static-deck-token-'));
  fixtureRoots.push(root);
  const deckDir = join(root, 'dist/_deck');
  await mkdir(deckDir, { recursive: true });
  await Promise.all(Object.entries(files).map(([name, html]) => (
    writeFile(join(deckDir, name), html, 'utf8')
  )));
  vi.spyOn(process, 'cwd').mockReturnValue(root);
  return deckDir;
}

afterEach(async () => {
  vi.restoreAllMocks();
  if (originalToken === undefined) delete process.env.VITE_DEV_TOKEN;
  else process.env.VITE_DEV_TOKEN = originalToken;
  await Promise.all(fixtureRoots.splice(0).map((root) => (
    rm(root, { recursive: true, force: true })
  )));
});

describe('static deck token plugin', () => {
  it('injects the shared token into both deck variants', async () => {
    const source = '<script>const token = __VITE_DEV_TOKEN__;</script>';
    const deckDir = await makeBuild({
      'index.html': source,
      'hub71.html': source,
    });
    process.env.VITE_DEV_TOKEN = '  shared-token  ';

    await staticDeckTokenPlugin().closeBundle();

    await expect(readFile(join(deckDir, 'index.html'), 'utf8'))
      .resolves.toContain('const token = "shared-token";');
    await expect(readFile(join(deckDir, 'hub71.html'), 'utf8'))
      .resolves.toContain('const token = "shared-token";');
  });

  it('escapes inline-script terminators and tolerates an absent deck variant', async () => {
    const deckDir = await makeBuild({
      'hub71.html': '<script>const token = __VITE_DEV_TOKEN__;</script>',
    });
    process.env.VITE_DEV_TOKEN = '</script>a\u2028b\u2029c';

    await staticDeckTokenPlugin().closeBundle();

    const html = await readFile(join(deckDir, 'hub71.html'), 'utf8');
    expect(html).toContain('"\\u003C/script\\u003Ea\\u2028b\\u2029c"');
    expect(html).not.toContain('</script>a\u2028');
  });
});
