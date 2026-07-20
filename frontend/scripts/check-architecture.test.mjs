import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, describe, expect, it } from 'vitest';

import {
  findArchitectureViolations,
  RATCHETED_SOURCE_LIMITS,
  RETIRED_SOURCE_PATHS,
} from './check-architecture.mjs';

const fixtureRoots = [];

const writeFixture = (root, relativePath, content) => {
  const fullPath = path.join(root, relativePath);
  fs.mkdirSync(path.dirname(fullPath), { recursive: true });
  fs.writeFileSync(fullPath, content, 'utf8');
};

const makeFixture = () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'taali-architecture-'));
  fixtureRoots.push(root);
  writeFixture(root, 'src/App.jsx', "export { default } from './AppShell';\n");
  writeFixture(root, 'src/AppShell.jsx', 'export default function AppShell() {}\n');
  return root;
};

afterEach(() => {
  for (const root of fixtureRoots.splice(0)) {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

describe('frontend architecture gate', () => {
  it('fails when a proven-dead frontend module is restored', () => {
    const root = makeFixture();
    const [retiredPath] = RETIRED_SOURCE_PATHS;
    writeFixture(root, retiredPath, 'export default function RetiredModule() {}\n');

    expect(findArchitectureViolations({ projectRoot: root })).toContain(
      `Retired frontend module restored: ${retiredPath}.`,
    );
  });

  it('fails when the real AppShell grows beyond its ratcheted baseline', () => {
    const root = makeFixture();
    const limit = RATCHETED_SOURCE_LIMITS.get('src/AppShell.jsx');
    writeFixture(root, 'src/AppShell.jsx', `${'// line\n'.repeat(limit)}// over baseline\n`);

    expect(findArchitectureViolations({ projectRoot: root })).toContain(
      `Ratcheted hotspot grew: src/AppShell.jsx has ${limit + 1} lines (max ${limit}).`,
    );
  });

  it('fails when lazy CSS redefines classes from canonical global owners', () => {
    const root = makeFixture();
    writeFixture(root, 'src/styles/08-shared-utilities.css', '.chip { display: inline-flex; }\n');
    writeFixture(root, 'src/styles/23-form-controls.css', '.taali-input { width: 100%; }\n');
    writeFixture(
      root,
      'src/features/candidates/BackgroundJobsToaster.css',
      '.bg-jobs-title { font-weight: 600; }\n',
    );
    writeFixture(
      root,
      'src/features/reports/LazyReport.css',
      '.chip { color: red; }\n.taali-input { border: 0; }\n.bg-jobs-title { color: blue; }\n',
    );

    const violations = findArchitectureViolations({ projectRoot: root });
    expect(violations).toEqual(expect.arrayContaining([
      expect.stringContaining('Global .chip CSS must stay in src/styles/08-shared-utilities.css'),
      expect.stringContaining('Global .taali-input CSS must stay in src/styles/23-form-controls.css'),
      expect.stringContaining(
        'Global .bg-jobs-title CSS must stay in src/features/candidates/BackgroundJobsToaster.css',
      ),
    ]));
  });

  it('allows page-qualified extensions of canonical utility classes', () => {
    const root = makeFixture();
    writeFixture(root, 'src/styles/08-shared-utilities.css', '.field { display: block; }\n');
    writeFixture(root, 'src/features/settings/SettingsPage.css', '.settings-panel .field { gap: 8px; }\n');

    expect(findArchitectureViolations({ projectRoot: root })).toEqual([]);
  });
});
