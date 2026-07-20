// @vitest-environment node
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'vite';
import viteConfig from '../../vite.config.js';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const repoRoot = path.resolve(frontendRoot, '..');
const configs = [
  path.join(repoRoot, 'vercel.json'),
  path.join(frontendRoot, 'vercel.json'),
];
const ciWorkflow = path.join(repoRoot, '.github', 'workflows', 'ci.yml');
const immutableValue = 'public, max-age=31536000, immutable';
const noStoreValue = 'private, no-store, max-age=0';
const immutableAssetSource = '/assets/(.*)-[A-Za-z0-9_-]{8}\\.[A-Za-z0-9]+';
const assetMissSource = '/assets(?:/(.*))?';
const expectedStaticRoutes = {
  '/agentic-hiring': '/agentic-hiring.html',
  '/ai-native-hiring': '/ai-native-hiring.html',
  '/ai-native-assessments': '/ai-native-assessments.html',
  '/report-preview': '/report-preview.html',
  '/home-preview': '/home-preview.html',
  '/jobs-preview': '/jobs-preview.html',
  '/pipeline-preview': '/pipeline-preview.html',
  '/search-preview': '/search-preview.html',
  '/analytics-preview': '/analytics-preview.html',
  '/settings-preview': '/settings-preview.html',
};

const readConfig = (configPath) => JSON.parse(fs.readFileSync(configPath, 'utf8'));

const cacheControlFor = (route) => Object.entries(route?.headers || {}).find(
  ([key]) => key.toLowerCase() === 'cache-control',
)?.[1];

const routeMatches = (route, pathname) => new RegExp(`^(?:${route.src})$`).test(pathname);

const listFiles = (directory) => fs.readdirSync(directory, { withFileTypes: true }).flatMap(
  (entry) => {
    const absolutePath = path.join(directory, entry.name);
    return entry.isDirectory() ? listFiles(absolutePath) : [absolutePath];
  },
);

let temporaryBuildRoot;

beforeAll(async () => {
  const temporaryDirectory = fs.realpathSync(
    fs.mkdtempSync(path.join(os.tmpdir(), 'taali-vercel-cache-')),
  );
  temporaryBuildRoot = path.join(temporaryDirectory, 'dist');
  fs.writeFileSync(
    path.join(temporaryDirectory, 'index.html'),
    '<div id="app"></div><script type="module" src="/main.js"></script>',
  );
  fs.writeFileSync(
    path.join(temporaryDirectory, 'main.js'),
    "import iconUrl from './mark.svg'; import './style.css'; document.body.dataset.icon = iconUrl;",
  );
  fs.writeFileSync(path.join(temporaryDirectory, 'style.css'), 'body { color: rebeccapurple; }');
  fs.writeFileSync(
    path.join(temporaryDirectory, 'mark.svg'),
    `<svg xmlns="http://www.w3.org/2000/svg"><desc>${'x'.repeat(5000)}</desc></svg>`,
  );
  await build({
    root: temporaryDirectory,
    configFile: false,
    logLevel: 'silent',
    build: {
      outDir: temporaryBuildRoot,
      emptyOutDir: true,
    },
  });
}, 10_000);

afterAll(() => {
  if (temporaryBuildRoot) {
    fs.rmSync(path.dirname(temporaryBuildRoot), { recursive: true, force: true });
  }
});

describe('Vercel asset caching', () => {
  it.each(configs)('caches only content-hashed assets before the filesystem lookup in %s', (configPath) => {
    const config = readConfig(configPath);
    const immutableIndex = config.routes.findIndex(
      (route) => route.src === immutableAssetSource,
    );
    const filesystemIndex = config.routes.findIndex((route) => route.handle === 'filesystem');
    const immutableRoute = config.routes[immutableIndex];

    expect(config.rewrites).toBeUndefined();
    expect(config.headers).toBeUndefined();
    expect(immutableIndex).toBeGreaterThanOrEqual(0);
    expect(immutableIndex).toBeLessThan(filesystemIndex);
    expect(immutableRoute).toMatchObject({ caseSensitive: true, continue: true });
    expect(cacheControlFor(immutableRoute)).toBe(immutableValue);
    expect(routeMatches(immutableRoute, '/assets/index-deadBEEF.js')).toBe(true);
    expect(routeMatches(immutableRoute, '/assets/theme.js')).toBe(false);
  });

  it.each(configs)('returns non-cacheable 404s for asset misses before the SPA fallback in %s', (configPath) => {
    const routes = readConfig(configPath).routes;
    const filesystemIndex = routes.findIndex((route) => route.handle === 'filesystem');
    const assetMissIndex = routes.findIndex((route) => route.src === assetMissSource);
    const spaIndex = routes.findIndex((route) => route.dest === '/index.html');
    const assetMissRoute = routes[assetMissIndex];

    expect(filesystemIndex).toBeLessThan(assetMissIndex);
    expect(assetMissIndex).toBeLessThan(spaIndex);
    expect(assetMissRoute).toMatchObject({ caseSensitive: true, status: 404 });
    expect(assetMissRoute.dest).toBeUndefined();
    expect(cacheControlFor(assetMissRoute)).toBe(noStoreValue);
    expect(routeMatches(assetMissRoute, '/assets/missing-deadBEEF.js')).toBe(true);
    expect(routeMatches(assetMissRoute, '/assets')).toBe(true);
    expect(routeMatches(assetMissRoute, '/jobs/123')).toBe(false);
    expect(routeMatches(routes[spaIndex], '/jobs/123')).toBe(true);
  });

  it.each(configs)('preserves named static pages and the non-asset SPA fallback in %s', (configPath) => {
    const routes = readConfig(configPath).routes;
    const staticRoutes = Object.fromEntries(
      routes.filter((route) => expectedStaticRoutes[route.src]).map(
        (route) => [route.src, route.dest],
      ),
    );

    expect(staticRoutes).toEqual(expectedStaticRoutes);
    expect(routes.at(-1)).toMatchObject({
      src: '/(.*)',
      caseSensitive: true,
      dest: '/index.html',
    });
  });

  it('keeps the application on Vite\'s content-hashed output defaults', () => {
    const output = viteConfig.build?.rollupOptions?.output || {};

    expect(viteConfig.build?.assetsDir).toBeUndefined();
    expect(output.entryFileNames).toBeUndefined();
    expect(output.chunkFileNames).toBeUndefined();
    expect(output.assetFileNames).toBeUndefined();
  });

  it('verifies hashes from a fresh Vite build instead of a stale dist directory', () => {
    const assetRoot = path.join(temporaryBuildRoot, 'assets');
    const builtAssets = listFiles(assetRoot);
    const hashPattern = /-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+$/;
    const immutableRoute = readConfig(configs[0]).routes.find(
      (route) => route.src === immutableAssetSource,
    );
    const assetUrls = builtAssets.map(
      (asset) => `/assets/${path.relative(assetRoot, asset).split(path.sep).join('/')}`,
    );

    expect(builtAssets.length).toBeGreaterThan(0);
    expect(builtAssets.filter((asset) => !hashPattern.test(path.basename(asset)))).toEqual([]);
    expect(assetUrls.filter((assetUrl) => !routeMatches(immutableRoute, assetUrl))).toEqual([]);
    expect(fs.existsSync(path.join(frontendRoot, 'public', 'theme.js'))).toBe(true);
    expect(fs.existsSync(path.join(frontendRoot, 'public', 'assets'))).toBe(false);
  });

  it('runs this regression in the targeted frontend CI list', () => {
    const workflow = fs.readFileSync(ciWorkflow, 'utf8');

    expect(workflow).toContain('src/test/vercelCaching.test.js');
  });
});
