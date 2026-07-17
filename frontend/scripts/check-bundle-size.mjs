import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import zlib from 'node:zlib';

const KiB = 1024;
export const BUNDLE_SIZE_BUDGETS = Object.freeze({
  js: { raw: 475 * KiB, gzip: 155 * KiB },
  css: { raw: 240 * KiB, gzip: 45 * KiB },
  // Vite names the primary application entry after its rollup input (`main`).
  // Retain the legacy `index` match so older build output receives the same
  // tighter ceiling rather than silently falling back to the general JS cap.
  entryJs: { raw: 240 * KiB, gzip: 80 * KiB },
  // An entry can stay small while eagerly importing several individually
  // compliant chunks. Bound the unique static dependency closure as well so
  // route-lazy charts/editors/graphs cannot silently return to every page.
  initialEntryJs: { raw: 500 * KiB, gzip: 165 * KiB },
});

const ENTRY_CHUNK_PREFIXES = ['main-', 'index-'];
const DEFERRED_INITIAL_CHUNK_PREFIXES = [
  'charts_vendor-',
  'graph_vendor-',
  'monaco_vendor-',
];

export const getBundleSizeBudget = (name) => {
  const extension = path.extname(name).slice(1);
  if (extension !== 'js' && extension !== 'css') return null;
  if (extension === 'js' && ENTRY_CHUNK_PREFIXES.some((prefix) => name.startsWith(prefix))) {
    return BUNDLE_SIZE_BUDGETS.entryJs;
  }
  return BUNDLE_SIZE_BUDGETS[extension];
};

const JAVASCRIPT_SCRIPT_TYPES = new Set([
  '',
  'module',
  'application/ecmascript',
  'application/javascript',
  'text/ecmascript',
  'text/javascript',
]);

const attributeValue = (tag, name) => {
  const match = tag.match(
    new RegExp(`\\b${name}\\s*=\\s*(?:["']([^"']+)["']|([^\\s>]+))`, 'iu'),
  );
  return match?.[1] ?? match?.[2] ?? null;
};

const initialScripts = (html) => {
  const scripts = [];
  for (const match of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script\s*>/giu)) {
    const tag = `<script${match[1]}>`;
    const type = (attributeValue(tag, 'type') ?? '')
      .split(';', 1)[0]
      .trim()
      .toLowerCase();
    if (!JAVASCRIPT_SCRIPT_TYPES.has(type)) continue;
    scripts.push({
      type,
      source: attributeValue(tag, 'src'),
      content: match[2],
    });
  }
  return scripts;
};

const staticImportSources = (javascript) => {
  const sources = [];
  const patterns = [
    /\bimport\s*(?!\()(?:[\w$*{},\s]+?\bfrom\s*)?["']([^"']+)["']/gu,
    /\bexport\s*(?:[\w$*{},\s]+?\bfrom\s*)["']([^"']+)["']/gu,
  ];
  for (const pattern of patterns) {
    for (const match of javascript.matchAll(pattern)) sources.push(match[1]);
  }
  return sources;
};

const localModulePath = ({ distRoot, importerPath, source }) => {
  const withoutSuffix = source.split(/[?#]/u, 1)[0];
  let resolved;
  if (withoutSuffix.startsWith('/')) {
    resolved = path.resolve(distRoot, `.${withoutSuffix}`);
  } else if (withoutSuffix.startsWith('.')) {
    resolved = path.resolve(path.dirname(importerPath), withoutSuffix);
  } else {
    return null;
  }
  const rootPrefix = `${path.resolve(distRoot)}${path.sep}`;
  if (!resolved.startsWith(rootPrefix) || path.extname(resolved) !== '.js') {
    return null;
  }
  return resolved;
};

export const collectInitialJavaScript = ({ distRoot, htmlPath }) => {
  const html = fs.readFileSync(htmlPath, 'utf8');
  const scripts = initialScripts(html);
  const pending = scripts
    .map(({ source }) => source)
    .filter(Boolean)
    .map((source) => localModulePath({ distRoot, importerPath: htmlPath, source }))
    .filter(Boolean);
  for (const script of scripts) {
    if (script.source || script.type !== 'module') continue;
    for (const source of staticImportSources(script.content)) {
      const dependency = localModulePath({
        distRoot,
        importerPath: htmlPath,
        source,
      });
      if (dependency) pending.push(dependency);
    }
  }
  const assets = new Set();

  while (pending.length) {
    const assetPath = pending.pop();
    const relativePath = path.relative(distRoot, assetPath).split(path.sep).join('/');
    if (assets.has(relativePath)) continue;
    if (!fs.existsSync(assetPath)) {
      throw new Error(`${path.basename(htmlPath)} references missing ${relativePath}`);
    }
    assets.add(relativePath);
    const javascript = fs.readFileSync(assetPath, 'utf8');
    for (const source of staticImportSources(javascript)) {
      const dependency = localModulePath({
        distRoot,
        importerPath: assetPath,
        source,
      });
      if (dependency) pending.push(dependency);
    }
  }
  return assets;
};

const initialInlineJavaScript = (html) => initialScripts(html)
  .filter(({ source, content }) => !source && content.trim())
  .map(({ content }) => content);

export const deferredInitialChunks = (assets) => [...assets]
  .map((asset) => path.basename(asset))
  .filter((name) => DEFERRED_INITIAL_CHUNK_PREFIXES.some((prefix) => name.startsWith(prefix)))
  .sort();

export const validateMotionFeatureChunk = ({ assetsRoot }) => {
  const chunks = fs.readdirSync(assetsRoot)
    .filter((name) => name.startsWith('motionFeatures-') && name.endsWith('.js'));
  if (chunks.length !== 1) {
    return `expected one deferred Motion feature chunk, found ${chunks.length}`;
  }

  // Import the exact production artifact in an isolated process. This catches
  // invalid cross-chunk initialisation that source-level tests cannot see and
  // that otherwise leaves every LazyMotion child invisible at runtime.
  const moduleUrl = pathToFileURL(path.join(assetsRoot, chunks[0])).href;
  const inspection = spawnSync(
    process.execPath,
    [
      '--input-type=module',
      '--eval',
      [
        `const loaded = await import(${JSON.stringify(moduleUrl)});`,
        'const features = loaded.default;',
        "if (!features || typeof features !== 'object' || typeof features.renderer !== 'function') process.exit(1);",
      ].join('\n'),
    ],
    { encoding: 'utf8', timeout: 10_000 },
  );
  if (inspection.status !== 0) {
    return `${chunks[0]} does not export an initialized Motion feature set`;
  }
  return null;
};

export const main = ({ projectRoot = path.resolve(process.cwd()) } = {}) => {
  const distRoot = path.join(projectRoot, 'dist');
  const assetsRoot = path.join(distRoot, 'assets');

  if (!fs.existsSync(assetsRoot)) {
    console.error('Bundle budget requires a completed `npm run build`.');
    return 1;
  }

  const violations = [];
  const motionFeatureViolation = validateMotionFeatureChunk({ assetsRoot });
  if (motionFeatureViolation) violations.push(motionFeatureViolation);
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

  for (const name of fs.readdirSync(distRoot).filter((item) => item.endsWith('.html'))) {
    const htmlPath = path.join(distRoot, name);
    const html = fs.readFileSync(htmlPath, 'utf8');
    let initialAssets;
    try {
      initialAssets = collectInitialJavaScript({ distRoot, htmlPath });
    } catch (error) {
      violations.push(error instanceof Error ? error.message : String(error));
      continue;
    }
    const inlineScripts = initialInlineJavaScript(html);
    if (!initialAssets.size && !inlineScripts.length) continue;

    const inlineData = Buffer.from(inlineScripts.join('\n'));
    let rawBytes = inlineData.length;
    let gzipBytes = inlineData.length
      ? zlib.gzipSync(inlineData, { level: 9 }).length
      : 0;
    for (const asset of initialAssets) {
      const data = fs.readFileSync(path.join(distRoot, asset));
      rawBytes += data.length;
      gzipBytes += zlib.gzipSync(data, { level: 9 }).length;
    }
    const budget = BUNDLE_SIZE_BUDGETS.initialEntryJs;
    if (rawBytes > budget.raw || gzipBytes > budget.gzip) {
      violations.push(
        `${name} initial JS: ${(rawBytes / KiB).toFixed(1)} KiB raw / ` +
        `${(gzipBytes / KiB).toFixed(1)} KiB gzip; budget ` +
        `${(budget.raw / KiB).toFixed(0)} / ${(budget.gzip / KiB).toFixed(0)} KiB`,
      );
    }
    const deferred = deferredInitialChunks(initialAssets);
    if (deferred.length) {
      violations.push(
        `${name} eagerly loads route-deferred chunks: ${deferred.join(', ')}`,
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
