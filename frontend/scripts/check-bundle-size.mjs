import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const projectRoot = path.resolve(process.cwd());
const assetsRoot = path.join(projectRoot, 'dist', 'assets');

if (!fs.existsSync(assetsRoot)) {
  console.error('Bundle budget requires a completed `npm run build`.');
  process.exit(1);
}

const KiB = 1024;
const budgets = {
  js: { raw: 475 * KiB, gzip: 155 * KiB },
  css: { raw: 240 * KiB, gzip: 45 * KiB },
  // Vite currently emits the application entry as one of the index-* chunks.
  // Keep both index chunks below the entry ceiling rather than depending on a
  // content hash or build-order-specific filename.
  indexJs: { raw: 240 * KiB, gzip: 80 * KiB },
};

const violations = [];
for (const name of fs.readdirSync(assetsRoot)) {
  const extension = path.extname(name).slice(1);
  if (extension !== 'js' && extension !== 'css') continue;
  const data = fs.readFileSync(path.join(assetsRoot, name));
  const gzipBytes = zlib.gzipSync(data, { level: 9 }).length;
  const budget = extension === 'js' && name.startsWith('index-')
    ? budgets.indexJs
    : budgets[extension];
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
  process.exit(1);
}

console.log('Bundle size budget passed.');
