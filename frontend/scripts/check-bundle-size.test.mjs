// @vitest-environment node

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  BUNDLE_SIZE_BUDGETS,
  collectInitialJavaScript,
  deferredInitialChunks,
  getBundleSizeBudget,
  main,
  validateMotionFeatureChunk,
} from './check-bundle-size.mjs';

const temporaryDirectories = [];

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe('bundle-size budget selection', () => {
  const writeMotionFeatureFixture = (source) => {
    const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'taali-motion-chunk-'));
    temporaryDirectories.push(projectRoot);
    const assetsRoot = path.join(projectRoot, 'dist', 'assets');
    fs.mkdirSync(assetsRoot, { recursive: true });
    fs.writeFileSync(path.join(projectRoot, 'package.json'), '{"type":"module"}');
    fs.writeFileSync(path.join(assetsRoot, 'motionFeatures-hash.js'), source);
    return assetsRoot;
  };

  it('accepts an initialized deferred Motion feature set', () => {
    const assetsRoot = writeMotionFeatureFixture(
      'export default { renderer() {}, animation: {} };',
    );

    expect(validateMotionFeatureChunk({ assetsRoot })).toBeNull();
  });

  it('rejects an uninitialized deferred Motion feature set', () => {
    const assetsRoot = writeMotionFeatureFixture('export default undefined;');

    expect(validateMotionFeatureChunk({ assetsRoot })).toContain(
      'does not export an initialized Motion feature set',
    );
  });

  it('applies the tighter entry budget to current main chunks', () => {
    expect(getBundleSizeBudget('main-content-hash.js')).toBe(BUNDLE_SIZE_BUDGETS.entryJs);
    expect(BUNDLE_SIZE_BUDGETS.entryJs).toEqual({ raw: 240 * 1024, gzip: 80 * 1024 });
  });

  it('preserves the tighter entry budget for legacy index chunks', () => {
    expect(getBundleSizeBudget('index-content-hash.js')).toBe(BUNDLE_SIZE_BUDGETS.entryJs);
  });

  it('keeps non-entry JavaScript on the general JavaScript budget', () => {
    expect(getBundleSizeBudget('feature-content-hash.js')).toBe(BUNDLE_SIZE_BUDGETS.js);
  });

  it('bounds the complete initial JavaScript closure', () => {
    expect(BUNDLE_SIZE_BUDGETS.initialEntryJs).toEqual({
      raw: 500 * 1024,
      gzip: 165 * 1024,
    });
  });

  it('follows static imports but leaves dynamic feature imports deferred', () => {
    const distRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'taali-bundle-'));
    temporaryDirectories.push(distRoot);
    const assetsRoot = path.join(distRoot, 'assets');
    fs.mkdirSync(assetsRoot);
    fs.writeFileSync(
      path.join(distRoot, 'index.html'),
      '<script type="module" src="/assets/main-hash.js"></script>',
    );
    fs.writeFileSync(
      path.join(assetsRoot, 'main-hash.js'),
      'import{render}from"./react_vendor-hash.js";import("./charts_vendor-hash.js");render();',
    );
    fs.writeFileSync(
      path.join(assetsRoot, 'react_vendor-hash.js'),
      'export{value}from"./runtime-hash.js";',
    );
    fs.writeFileSync(path.join(assetsRoot, 'runtime-hash.js'), 'export const value=1;');
    fs.writeFileSync(path.join(assetsRoot, 'charts_vendor-hash.js'), 'export const chart=1;');

    const assets = collectInitialJavaScript({
      distRoot,
      htmlPath: path.join(distRoot, 'index.html'),
    });

    expect([...assets].sort()).toEqual([
      'assets/main-hash.js',
      'assets/react_vendor-hash.js',
      'assets/runtime-hash.js',
    ]);
    expect(deferredInitialChunks(assets)).toEqual([]);
    expect(deferredInitialChunks(new Set(['assets/charts_vendor-hash.js']))).toEqual([
      'charts_vendor-hash.js',
    ]);
  });

  it('includes classic local scripts and inline-module imports but ignores JSON-LD', () => {
    const distRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'taali-bundle-classic-'));
    temporaryDirectories.push(distRoot);
    const assetsRoot = path.join(distRoot, 'assets');
    fs.mkdirSync(assetsRoot);
    fs.writeFileSync(
      path.join(distRoot, 'marketing.html'),
      [
        '<script src="/theme.js" defer></script>',
        '<script type="module">import "/assets/runtime-hash.js";</script>',
        '<script type="application/ld+json" src="/missing-schema.js">{}</script>',
      ].join(''),
    );
    fs.writeFileSync(path.join(distRoot, 'theme.js'), 'document.body.dataset.theme="light";');
    fs.writeFileSync(path.join(assetsRoot, 'runtime-hash.js'), 'export const value=1;');

    const assets = collectInitialJavaScript({
      distRoot,
      htmlPath: path.join(distRoot, 'marketing.html'),
    });

    expect([...assets].sort()).toEqual([
      'assets/runtime-hash.js',
      'theme.js',
    ]);
  });

  it('counts executable inline JavaScript against the initial budget', () => {
    const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'taali-inline-gate-'));
    temporaryDirectories.push(projectRoot);
    const distRoot = path.join(projectRoot, 'dist');
    fs.mkdirSync(path.join(distRoot, 'assets'), { recursive: true });
    fs.writeFileSync(
      path.join(distRoot, 'marketing.html'),
      `<script>/*${'x'.repeat(BUNDLE_SIZE_BUDGETS.initialEntryJs.raw)}*/</script>`,
    );
    const errors = [];
    const error = vi.spyOn(console, 'error').mockImplementation((message) => {
      errors.push(String(message));
    });

    try {
      expect(main({ projectRoot })).toBe(1);
    } finally {
      error.mockRestore();
    }
    expect(errors.join('\n')).toContain('marketing.html initial JS');
  });

  it('fails closed when a route-deferred chunk enters an HTML startup closure', () => {
    const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'taali-bundle-gate-'));
    temporaryDirectories.push(projectRoot);
    const distRoot = path.join(projectRoot, 'dist');
    const assetsRoot = path.join(distRoot, 'assets');
    fs.mkdirSync(assetsRoot, { recursive: true });
    fs.writeFileSync(
      path.join(distRoot, 'index.html'),
      '<script type="module" src="/assets/main-hash.js"></script>',
    );
    fs.writeFileSync(
      path.join(assetsRoot, 'main-hash.js'),
      'import"./charts_vendor-hash.js";',
    );
    fs.writeFileSync(path.join(assetsRoot, 'charts_vendor-hash.js'), 'export const chart=1;');
    const errors = [];
    const error = vi.spyOn(console, 'error').mockImplementation((message) => {
      errors.push(String(message));
    });

    try {
      expect(main({ projectRoot })).toBe(1);
    } finally {
      error.mockRestore();
    }
    expect(errors.join('\n')).toContain(
      'index.html eagerly loads route-deferred chunks: charts_vendor-hash.js',
    );
  });
});
