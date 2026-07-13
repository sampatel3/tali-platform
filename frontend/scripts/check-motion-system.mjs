#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';

const projectRoot = path.resolve(process.cwd());
const sourceRoot = path.join(projectRoot, 'src');
const publicRoot = path.join(projectRoot, 'public');
const motionRoot = path.join(sourceRoot, 'shared', 'motion');
const sourceExtensions = new Set(['.css', '.html', '.js', '.jsx', '.ts', '.tsx']);
const violations = [];

const lineFor = (content, index) => content.slice(0, index).split('\n').length;

const walk = (dir, files = []) => {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue;
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(fullPath, files);
    else if (sourceExtensions.has(path.extname(entry.name))) files.push(fullPath);
  }
  return files;
};

for (const fullPath of [...walk(sourceRoot), ...walk(publicRoot)]) {
  const relativePath = path.relative(projectRoot, fullPath);
  const content = fs.readFileSync(fullPath, 'utf8');
  const isMotionCore = fullPath.startsWith(`${motionRoot}${path.sep}`);
  const isTest = /\.test\.[jt]sx?$/.test(fullPath);

  const checks = [
    {
      pattern: /@keyframes\s+[\w-]+/g,
      message: 'CSS keyframes are not allowed; use a shared Motion primitive',
      enabled: !isMotionCore,
    },
    {
      pattern: /\banimation(?:-name)?\s*:/g,
      message: 'CSS animation declarations are not allowed; use a shared Motion primitive',
      enabled: !isMotionCore,
    },
    {
      pattern: /\banimate-(?:spin|pulse)\b/g,
      message: 'Tailwind animation utilities are not allowed; use MotionLoop or MotionSpinner',
      enabled: !isTest,
    },
    {
      pattern: /from\s+['"]motion\/react['"]/g,
      message: 'import Motion through src/shared/motion',
      enabled: !isMotionCore,
    },
    {
      pattern: /shared\/motion\/reveal\.css/g,
      message: 'legacy reveal.css is retired; use Reveal or MotionStagger',
      enabled: true,
    },
    {
      pattern: /className\s*=\s*['"][^'"]*\breveal-stagger\b[^'"]*['"]/g,
      message: 'legacy reveal-stagger is retired; use MotionStagger',
      enabled: !isTest,
    },
  ];

  for (const check of checks) {
    if (!check.enabled) continue;
    for (const match of content.matchAll(check.pattern)) {
      violations.push(`${relativePath}:${lineFor(content, match.index)} — ${check.message}`);
    }
  }
}

const readTokenObject = (content, exportName) => {
  const block = content.match(new RegExp(`export const ${exportName} = Object\\.freeze\\(\\{([\\s\\S]*?)\\}\\);`));
  if (!block) return new Map();
  return new Map([...block[1].matchAll(/^\s{2}([\w]+):\s*([\d.]+),?$/gm)]
    .map((match) => [match[1], Number(match[2])]));
};

const tokensSource = fs.readFileSync(path.join(motionRoot, 'tokens.js'), 'utf8');
const motionCss = fs.readFileSync(path.join(motionRoot, 'motion.css'), 'utf8');
const cssTokens = new Map();
for (const match of motionCss.matchAll(/(--motion-[\w-]+):\s*([\d.]+)(ms|s);/g)) {
  // The first declaration is the canonical value. Later 1ms declarations are
  // the intentional reduced-motion override in the media query.
  if (!cssTokens.has(match[1])) {
    cssTokens.set(match[1], { value: Number(match[2]), unit: match[3] });
  }
}

const parityGroups = [
  { object: 'MOTION_DURATION', prefix: '--motion-duration-', unit: 'ms', multiplier: 1000 },
  { object: 'AGENT_LOOP_DURATION', prefix: '--motion-agent-', unit: 's', multiplier: 1 },
  { object: 'MOTION_STAGGER', prefix: '--motion-stagger-', unit: 'ms', multiplier: 1000 },
];

for (const group of parityGroups) {
  for (const [name, jsValue] of readTokenObject(tokensSource, group.object)) {
    if (group.object === 'MOTION_STAGGER' && name === 'maxItems') continue;
    const cssName = `${group.prefix}${name}`;
    const cssValue = cssTokens.get(cssName);
    const expected = jsValue * group.multiplier;
    if (!cssValue || cssValue.unit !== group.unit || Math.abs(cssValue.value - expected) > 0.001) {
      violations.push(`src/shared/motion/tokens.js — ${group.object}.${name} must match ${cssName} in motion.css`);
    }
  }
}

if (violations.length) {
  console.error('Motion-system guard failed:');
  violations.forEach((violation) => console.error(`- ${violation}`));
  process.exit(1);
}

console.log('Motion-system guard passed.');
