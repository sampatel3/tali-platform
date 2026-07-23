// @vitest-environment node
//
// Guards the fix for the 2026-07-20 incident: the assessment CSP shipped
// without an allowlist for cdn.jsdelivr.net while @monaco-editor/react was
// still loading the Monaco runtime from there, and the code editor was dead on
// every surface for three days. Nothing failed loudly — the loader logs to the
// console and leaves the candidate on "Loading editor...".
//
// Monaco is now bundled and served from our own origin, so the CDN allowlist is
// gone for good. These assertions fail the build if either half of that pairing
// is undone.
import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const repoRoot = path.resolve(frontendRoot, '..');

const vercelConfigs = {
  'vercel.json': path.join(repoRoot, 'vercel.json'),
  'frontend/vercel.json': path.join(frontendRoot, 'vercel.json'),
};
const ASSESSMENT_ROUTES = ['/assess/(.*)', '/assessment/(.*)'];

const read = (filePath) => fs.readFileSync(filePath, 'utf8');

const cspFor = (config, src) => {
  const route = config.routes.find((entry) => entry.src === src);
  const header = Object.entries(route?.headers || {}).find(
    ([key]) => key.toLowerCase() === 'content-security-policy',
  );
  return header?.[1];
};

const listSourceFiles = (directory) => fs.readdirSync(directory, { withFileTypes: true }).flatMap(
  (entry) => {
    const absolutePath = path.join(directory, entry.name);
    if (entry.isDirectory()) return listSourceFiles(absolutePath);
    return /\.(js|jsx|ts|tsx)$/.test(entry.name) ? [absolutePath] : [];
  },
);

describe('Monaco is self-hosted', () => {
  it('bundles the Monaco runtime as a direct dependency', () => {
    const pkg = JSON.parse(read(path.join(frontendRoot, 'package.json')));
    expect(pkg.dependencies['monaco-editor']).toBeTruthy();
  });

  it('hands the bundled runtime to the loader instead of letting it fetch one', () => {
    const setup = read(path.join(frontendRoot, 'src/components/assessment/monacoSetup.js'));
    expect(setup).toMatch(/import \* as monaco from 'monaco-editor/);
    expect(setup).toMatch(/loader\.config\(\{\s*monaco\s*\}\)/);
    // A paths/vs override would put us straight back on a remote runtime.
    expect(setup).not.toMatch(/https?:\/\//);
  });

  it('wires a worker for every language service that needs one', () => {
    const setup = read(path.join(frontendRoot, 'src/components/assessment/monacoSetup.js'));
    expect(setup).toMatch(/editor\.worker\?worker/);
    expect(setup).toMatch(/json\.worker\?worker/);
    expect(setup).toMatch(/ts\.worker\?worker/);
    expect(setup).toMatch(/MonacoEnvironment/);
  });

  it('registers every language the workspace can open', () => {
    // Bundling only what we import means a new entry in languageFromPath now
    // silently downgrades to no highlighting instead of being lazily fetched.
    // Fail here so the language gets registered alongside it.
    const helpers = read(
      path.join(frontendRoot, 'src/features/assessment_runtime/assessmentRuntimeHelpers.js'),
    );
    const declaration = helpers.slice(helpers.indexOf('export function languageFromPath'));
    const body = declaration.slice(0, declaration.indexOf('\n}'));
    const languages = [...new Set([...body.matchAll(/return '([a-z]+)'/g)].map((m) => m[1]))];
    expect(languages).toContain('python');

    const setup = read(path.join(frontendRoot, 'src/components/assessment/monacoSetup.js'));
    const builtIn = new Set(['plaintext']);
    const missing = languages.filter((language) => !builtIn.has(language)
      && !setup.includes(`/basic-languages/${language}/${language}.contribution`)
      && !setup.includes(`/language/${language}/monaco.contribution`));
    expect(missing).toEqual([]);
  });

  it('loads the setup module from the editor component', () => {
    const editor = read(path.join(frontendRoot, 'src/components/assessment/CodeEditor.jsx'));
    expect(editor).toMatch(/import '\.\/monacoSetup'/);
  });

  it('keeps the Monaco runtime in its own lazily-loaded chunk', () => {
    const viteConfig = read(path.join(frontendRoot, 'vite.config.js'));
    expect(viteConfig).toMatch(/monaco_vendor:/);
    // Seeding the chunk with the package entry (editor.main) pulls in every
    // language Monaco ships, whatever monacoSetup.js actually imports.
    expect(viteConfig).not.toMatch(/monaco_vendor:.*'monaco-editor'/);

    const workspace = read(
      path.join(frontendRoot, 'src/features/assessment_runtime/AssessmentWorkspace.jsx'),
    );
    expect(workspace).toMatch(/lazy\(\(\) => import\('\.\.\/\.\.\/components\/assessment\/CodeEditor'\)\)/);
  });

  it('surfaces a fallback editor instead of hanging when Monaco never mounts', () => {
    const editor = read(path.join(frontendRoot, 'src/components/assessment/CodeEditor.jsx'));
    expect(editor).toMatch(/MOUNT_TIMEOUT_MS/);
    expect(editor).toMatch(/throw new Error\('Monaco editor failed to mount'\)/);
  });
});

describe('assessment CSP allows no third-party CDN', () => {
  for (const [label, configPath] of Object.entries(vercelConfigs)) {
    for (const routeSrc of ASSESSMENT_ROUTES) {
      it(`${label} ${routeSrc} serves scripts, styles and fonts without a CDN`, () => {
        const csp = cspFor(JSON.parse(read(configPath)), routeSrc);
        expect(csp).toBeTruthy();
        expect(csp).not.toContain('cdn.jsdelivr.net');
        expect(csp).toContain("script-src 'self' 'unsafe-inline';");
        // Monaco runs its language services in workers off our own origin.
        expect(csp).toContain("worker-src 'self' blob:");
      });
    }
  }

  it('leaves no CDN reference anywhere in the application source', () => {
    // Matches a real URL rather than the bare domain, so incident notes in
    // comments stay readable while a reintroduced runtime fetch fails here.
    const offenders = listSourceFiles(path.join(frontendRoot, 'src'))
      .filter((filePath) => read(filePath).includes('https://cdn.jsdelivr.net'))
      .map((filePath) => path.relative(frontendRoot, filePath))
      .filter((relativePath) => relativePath !== 'src/test/monacoSelfHosted.test.js');
    expect(offenders).toEqual([]);
  });
});
