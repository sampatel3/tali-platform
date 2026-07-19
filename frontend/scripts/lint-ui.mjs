#!/usr/bin/env node
/**
 * Guardrail script for the TAALI token-era UI policy.
 *
 * The application source is checked for component-policy violations. Static
 * HTML experiences (content pages, previews, and the investor deck) are
 * separate token islands, so they are checked for unresolved CSS custom
 * properties against their own definitions without pretending their vanilla
 * JavaScript is React component code. Test fixtures and generated/vendored
 * sources do not ship as application UI and are intentionally outside scope.
 *
 * The token debt is zero, so every finding is a regression. There is no
 * allowlist or ratchet file to weaken this policy.
 */

import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const ROOT = join(__dirname, '..');
const IGNORE_DIR_NAMES = new Set(['node_modules', '.git', 'dist', 'coverage']);
const FILE_PATTERN = /\.(?:jsx?|tsx?|css|mjs|html)$/;
const COMPONENT_FILE_PATTERN = /\.(?:jsx?|tsx?)$/;
const TEST_FILE_PATTERN = /(?:^|\/)(?:__tests__\/|[^/]+\.(?:test|spec)\.(?:jsx?|tsx?))$/;

const APP_SCOPE = {
  name: 'application',
  mode: 'application',
  roots: ['src'],
};

// Each public experience owns its CSS cascade. Keeping these scopes separate
// prevents a token in one static page from falsely satisfying another page.
const STATIC_SCOPES = [
  {
    name: 'static-content-pages',
    mode: 'css-variables-only',
    files: [
      'public/styles/content.css',
      'public/theme.js',
      'public/agentic-hiring.html',
      'public/ai-native-assessments.html',
      'public/ai-native-hiring.html',
    ],
  },
  {
    name: 'investor-deck',
    mode: 'css-variables-only',
    roots: ['public/_deck'],
  },
  {
    name: 'pipeline-preview',
    mode: 'css-variables-only',
    files: ['public/report-preview.css', 'public/pipeline-preview.html'],
  },
  {
    name: 'search-preview',
    mode: 'css-variables-only',
    files: ['public/report-preview.css', 'public/search-preview.html'],
  },
  {
    name: 'settings-preview',
    mode: 'css-variables-only',
    files: ['public/report-preview.css', 'public/settings-preview.html'],
  },
  {
    name: 'legacy-report-preview-styles',
    mode: 'css-variables-only',
    files: ['public/report-preview.css'],
  },
];

const CANONICAL_THEME_TOGGLE_FILES = new Set([
  'src/shared/ui/ThemeModeToggle.jsx',
  'src/shared/ui/GlobalThemeToggle.jsx',
]);

const BORDER2_PATTERN = /\bborder-2\b/g;
const HEX_PATTERN = /#(?:[0-9a-fA-F]{3,8})\b/g;
const RAW_UTILITY_PATTERN = /\b(?:bg|text|border)-(?:white|black|gray(?:-\d{2,3})?)(?:\/\d{1,3})?\b/g;
const THEME_TOGGLE_PATTERN = /\b(?:Switch to light theme|Switch to dark theme|Light UI|Dark UI)\b/g;
const GRADIENT_BG_VAR_PATTERN = /\bbg-\[var\(--[^)\]]*gradient[^)\]]*\)\]/g;
const SQUARE_TABLE_PATTERN = /\b(?:rounded-none|rounded-sm)\b/g;
const DIRECT_MOTION_IMPORT_PATTERN = /from\s+['"]motion\/react['"]/g;
const LEGACY_COUNTUP_IMPORT_PATTERN = /from\s+['"][^'"]*shared\/motion\/useCountUp['"]/g;
const RETIRED_AGENT_KEYFRAME_PATTERN = /@keyframes\s+(?:abar(?:Pulse|Flow|Ring)|agentChipPulse|mc-(?:aurora|pulse-ring|blink)|mc(?:AgentFlow|VgFlow)|rqRecFlow|drFlow|aw-pulse|bgJobsPulse|agzSoft|an-pulse|ac-pulse|tk-dot|lv[fg]AgentFlow|lvc(?:SwitchFlow|Ring|RibbonFlow|NodePulse)|lvd(?:SwitchFlow|Ring))\b/gi;
const CSS_VAR_DEFINITION_PATTERN = /(?:^|[\s{;,])['"]?(--[\w-]+)['"]?\s*:/g;
const CSS_VAR_SET_PROPERTY_PATTERN = /\.setProperty\(\s*['"](--[\w-]+)['"]/g;

const lineNumberForIndex = (content, index) => content.slice(0, index).split('\n').length;
const makeFinding = (file, content, index, rule, value, message) => ({
  file,
  line: lineNumberForIndex(content, index),
  rule,
  value,
  message,
});

const isIgnored = (fullPath) => fullPath
  .split('/')
  .some((segment) => IGNORE_DIR_NAMES.has(segment));

async function walk(dir, files = []) {
  const entries = await readdir(dir, { withFileTypes: true });
  for (const entry of entries) {
    const fullPath = join(dir, entry.name);
    if (isIgnored(fullPath)) continue;
    if (entry.isDirectory()) {
      await walk(fullPath, files);
      continue;
    }
    if (entry.isFile() && FILE_PATTERN.test(entry.name)) {
      files.push(relative(ROOT, fullPath));
    }
  }
  return files;
}

async function filesForScope(scope) {
  const files = [...(scope.files || [])];
  for (const root of scope.roots || []) {
    await walk(join(ROOT, root), files);
  }
  return [...new Set(files)].sort();
}

export function findCssVariableDefinitions(content) {
  const definitions = new Set();
  for (const pattern of [CSS_VAR_DEFINITION_PATTERN, CSS_VAR_SET_PROPERTY_PATTERN]) {
    const matcher = new RegExp(pattern.source, pattern.flags);
    let match;
    while ((match = matcher.exec(content)) !== null) {
      definitions.add(match[1]);
    }
  }
  return definitions;
}

/**
 * Finds every var() call, including nested calls, and records whether the
 * referenced property has a fallback. A small balanced-parenthesis scanner is
 * used because a regex cannot correctly handle var(--a, var(--b, value)).
 */
export function findCssVariableUsages(content) {
  const usages = [];
  let searchFrom = 0;
  while (searchFrom < content.length) {
    const start = content.indexOf('var(', searchFrom);
    if (start === -1) break;

    let depth = 1;
    let quote = null;
    let escaped = false;
    let end = start + 4;
    for (; end < content.length; end += 1) {
      const char = content[end];
      if (escaped) {
        escaped = false;
        continue;
      }
      if (char === '\\') {
        escaped = true;
        continue;
      }
      if (quote) {
        if (char === quote) quote = null;
        continue;
      }
      if (char === '"' || char === "'") {
        quote = char;
        continue;
      }
      if (char === '(') depth += 1;
      if (char === ')') {
        depth -= 1;
        if (depth === 0) break;
      }
    }

    if (depth === 0) {
      const expression = content.slice(start + 4, end);
      const nameMatch = expression.match(/^\s*(--[\w-]+)/);
      if (nameMatch) {
        const remainder = expression.slice(nameMatch[0].length).trimStart();
        // A template such as `var(--v${index})` becomes a concrete variable at
        // runtime. Do not misreport its partial source prefix (`--v`) as a
        // custom property; the concrete declarations/usages remain checked.
        if (remainder === '' || remainder.startsWith(',')) {
          usages.push({
            index: start,
            name: nameMatch[1],
            hasFallback: remainder.startsWith(','),
          });
        }
      }
    }

    // Advance only past this `var(` so nested calls are found as well.
    searchFrom = start + 4;
  }
  return usages;
}

export function collectCssVariableViolations(file, content, definitions) {
  return findCssVariableUsages(content)
    .filter(({ name, hasFallback }) => !hasFallback && !definitions.has(name))
    .map(({ index, name }) => makeFinding(
      file,
      content,
      index,
      'undefined-css-variable',
      name,
      `undefined CSS variable "${name}" without a fallback`,
    ));
}

function collectPatternFindings(file, content, pattern, rule, messageForValue) {
  const findings = [];
  const matcher = new RegExp(pattern.source, pattern.flags);
  let match;
  while ((match = matcher.exec(content)) !== null) {
    const value = match[0];
    findings.push(makeFinding(file, content, match.index, rule, value, messageForValue(value)));
  }
  return findings;
}

export function collectApplicationViolations(file, content) {
  const findings = [];

  if (!file.startsWith('src/shared/motion/')) {
    findings.push(...collectPatternFindings(
      file,
      content,
      DIRECT_MOTION_IMPORT_PATTERN,
      'direct-motion-import',
      () => 'import Motion through src/shared/motion',
    ));
  }
  findings.push(...collectPatternFindings(
    file,
    content,
    LEGACY_COUNTUP_IMPORT_PATTERN,
    'legacy-countup-import',
    () => 'use MotionNumber from src/shared/motion',
  ));
  findings.push(...collectPatternFindings(
    file,
    content,
    RETIRED_AGENT_KEYFRAME_PATTERN,
    'retired-agent-keyframe',
    () => 'live agent loops must use AgentLoop from src/shared/motion',
  ));

  if (!COMPONENT_FILE_PATTERN.test(file)) return findings;

  findings.push(...collectPatternFindings(
    file,
    content,
    BORDER2_PATTERN,
    'border-2-surface',
    () => 'raw border-2 surface styling should be replaced with the shared surface treatment',
  ));
  findings.push(...collectPatternFindings(
    file,
    content,
    HEX_PATTERN,
    'hardcoded-hex',
    (value) => `hardcoded hex color "${value}" should be replaced with a semantic token`,
  ));
  findings.push(...collectPatternFindings(
    file,
    content,
    RAW_UTILITY_PATTERN,
    'raw-color-utility',
    (value) => `raw ${value} utility should be replaced with a semantic token`,
  ));

  if (!CANONICAL_THEME_TOGGLE_FILES.has(file)) {
    findings.push(...collectPatternFindings(
      file,
      content,
      THEME_TOGGLE_PATTERN,
      'duplicate-theme-toggle',
      () => 'theme toggle text should only live in the shared toggle primitives',
    ));
  }
  findings.push(...collectPatternFindings(
    file,
    content,
    GRADIENT_BG_VAR_PATTERN,
    'gradient-token-utility',
    () => 'gradient tokens should not be passed through bg-[var(...)] utilities',
  ));

  if (content.includes('TableShell') || content.includes('<table') || content.includes('overflow-x-auto')) {
    findings.push(...collectPatternFindings(
      file,
      content,
      SQUARE_TABLE_PATTERN,
      'square-table-rounding',
      () => 'square table-shell rounding is not allowed',
    ));
  }

  return findings;
}

async function collectScopeFindings(scope) {
  const files = (await filesForScope(scope)).filter((file) => !TEST_FILE_PATTERN.test(file));
  const contents = new Map();
  const definitions = new Set();

  for (const file of files) {
    const content = await readFile(join(ROOT, file), 'utf8');
    contents.set(file, content);
    for (const definition of findCssVariableDefinitions(content)) {
      definitions.add(definition);
    }
  }

  const findings = [];
  for (const [file, content] of contents) {
    findings.push(...collectCssVariableViolations(file, content, definitions));
    if (scope.mode === 'application') {
      findings.push(...collectApplicationViolations(file, content));
    }
  }
  return findings;
}

async function collectAllFindings() {
  const scopes = [APP_SCOPE, ...STATIC_SCOPES];
  const findings = [];
  for (const scope of scopes) {
    findings.push(...await collectScopeFindings(scope));
  }
  return findings.sort((a, b) => (
    a.file.localeCompare(b.file)
    || a.line - b.line
    || a.rule.localeCompare(b.rule)
  ));
}

async function main() {
  const findings = await collectAllFindings();

  if (process.argv.includes('--print-findings')) {
    console.log(JSON.stringify(findings, null, 2));
    return;
  }
  if (findings.length > 0) {
    console.error('lint:ui failed — token and component policy violations:\n');
    findings.slice(0, 80).forEach((finding) => {
      console.error(`  ${finding.file}:${finding.line} — [${finding.rule}] ${finding.message}`);
    });
    if (findings.length > 80) {
      console.error(`  ... and ${findings.length - 80} more`);
    }
    process.exitCode = 1;
    return;
  }

  console.log('lint:ui: OK — zero token or component-policy violations');
}

const isDirectExecution = process.argv[1]
  && pathToFileURL(process.argv[1]).href === import.meta.url;

if (isDirectExecution) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
