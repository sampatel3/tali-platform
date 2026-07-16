import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';

const KiB = 1024;
export const BUNDLE_SIZE_BUDGETS = Object.freeze({
  js: { raw: 475 * KiB, gzip: 155 * KiB },
  css: { raw: 240 * KiB, gzip: 45 * KiB },
  // Vite names the primary application entry after its rollup input (`main`).
  // Retain the legacy `index` match so older build output receives the same
  // tighter ceiling rather than silently falling back to the general JS cap.
  entryJs: { raw: 240 * KiB, gzip: 80 * KiB },
});

const ENTRY_CHUNK_PREFIXES = ['main-', 'index-'];

export const getBundleSizeBudget = (name) => {
  const extension = path.extname(name).slice(1);
  if (extension !== 'js' && extension !== 'css') return null;
  if (extension === 'js' && ENTRY_CHUNK_PREFIXES.some((prefix) => name.startsWith(prefix))) {
    return BUNDLE_SIZE_BUDGETS.entryJs;
  }
  return BUNDLE_SIZE_BUDGETS[extension];
};

export const main = ({ projectRoot = path.resolve(process.cwd()) } = {}) => {
  const assetsRoot = path.join(projectRoot, 'dist', 'assets');

  if (!fs.existsSync(assetsRoot)) {
    console.error('Bundle budget requires a completed `npm run build`.');
    return 1;
  }

  const violations = [];
  for (const name of fs.readdirSync(assetsRoot)) {
    const budget = getBundleSizeBudget(name);
    if (!budget) continue;
    const data = fs.readFileSync(path.join(assetsRoot, name));
    const gzipBytes = zlib.gzipSync(data, { level: 9 }).length;
    if (data.length > budget.raw || gzipBytes > budget.gzip) {
      violations.push(
        `${name}: ${(data.length / KiB).toFixed(1)} KiB raw / ` +
        `${(gzipBytes / KiB).toFixed(1)} KiB gzip; budget ` +
        `${(budget.raw / KiB).toFixed(0)} / ${(budget.gzip / KiB).toFixed(0)} KiB`,
      );
    }
  }

  if (violations.length) {
    console.error('Bundle size budget failed:');
    violations.forEach((violation) => console.error(`- ${violation}`));
    return 1;
  }

  console.log('Bundle size budget passed.');
  return 0;
};

const isDirectRun = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectRun) process.exitCode = main();
