import { readFile } from 'node:fs/promises';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const scriptPath = path.join(
  process.cwd(),
  'scripts/generate-data-engineer-fixtures.mjs',
);

describe('landing fixture generator provider contract', () => {
  it('defaults to the reviewed supported model, never the retired Opus alias', async () => {
    const source = await readFile(scriptPath, 'utf8');

    expect(source).toContain("process.env.ANTHROPIC_MODEL || 'claude-opus-4-8'");
    expect(source).not.toContain("process.env.ANTHROPIC_MODEL || 'claude-3-opus-latest'");
    expect(source).not.toContain('temperature:');
  });
});
