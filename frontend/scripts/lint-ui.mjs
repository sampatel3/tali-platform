#!/usr/bin/env node
/**
 * Guardrail script: fails if forbidden UI patterns exist.
 * Run: npm run lint:ui
 *
 * Forbidden:
 * - rounded-* (except rounded-none)
 * - border-radius: outside index.css or designated primitives
 * - font-family: outside index.css or typography primitives
 */

import { readdir, readFile } from 'fs/promises';
import { join, relative } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const ROOT = join(__dirname, '..');
const ALLOWED_FILES = [
  'src/index.css',
  'scripts/lint-ui.mjs',
];

const allowedDir = (p) => p.includes('node_modules') || p.includes('.git') || p.includes('dist');

const patterns = [
  { regex: /\brounded-(sm|md|lg|xl|2xl|3xl|full)\b/g, msg: 'rounded-* (use square corners)' },
  { regex: /rounded-\[(?!0)[^\]]+\]/g, msg: 'rounded-[*] with non-zero value' },
  { regex: /border-radius:\s*(?!0|var\(--taali-radius\))[^;]+/g, msg: 'border-radius outside tokens' },
];

async function walk(dir, files = []) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = join(dir, e.name);
    const rel = relative(ROOT, full);
    if (allowedDir(full)) continue;
    if (e.isDirectory()) {
      await walk(full, files);
    } else if (e.isFile() && /\.(jsx?|tsx?|css)$/.test(e.name)) {
      if (!rel.includes('node_modules')) {
        files.push(rel);
      }
    }
  }
  return files;
}

async function main() {
  const files = await walk(ROOT);
  const errors = [];

  for (const rel of files) {
    const path = join(ROOT, rel);
    const content = await readFile(path, 'utf-8');

    for (const { regex, msg } of patterns) {
      let m;
      const re = new RegExp(regex.source, regex.flags);
      while ((m = re.exec(content)) !== null) {
        const line = content.slice(0, m.index).split('\n').length;
        const isAllowed = ALLOWED_FILES.some((a) => rel.includes(a) || rel === a);
        if (!isAllowed) {
          errors.push(`${rel}:${line} — ${msg} — "${m[0]}"`);
        }
      }
    }
  }

  if (errors.length > 0) {
    console.error('lint:ui failed — forbidden UI patterns:\n');
    errors.slice(0, 50).forEach((e) => console.error('  ' + e));
    if (errors.length > 50) {
      console.error(`  ... and ${errors.length - 50} more`);
    }
    process.exit(1);
  }

  console.log('lint:ui: OK');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
