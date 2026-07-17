import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEFAULT_PROJECT_ROOT = path.resolve(process.cwd());

const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx']);
// Matches `*Page.jsx` AND `*PageContent.jsx` so a page cannot dodge the cap
// by moving its body behind a one-line re-export.
const PAGE_FILE_PATTERN = /Page(Content)?\.(js|jsx|ts|tsx)$/;
// New pages and the eventual split children of legacy pages must stay small.
const DISALLOWED_IMPORT_PATTERNS = [
  /from\s+['"][^'"]*lib\/api(?:\.js)?['"]/,
  /import\s*\(\s*['"][^'"]*lib\/api(?:\.js)?['"]\s*\)/,
];
const HASH_ROUTE_FALLBACK_PATTERNS = [
  /\bwindow\.location\.hash\b/,
  /\bHashRouter\b/,
  /\blocation\.hash\.(?:slice|substring|replace)\s*\(/,
];

export const MAX_NEW_PAGE_LINES = 500;
export const APP_ENTRY_MAX_LINES = 500;

// Burn-down caps for the app shell and known legacy pages. New pages never get
// entries here: they must remain below MAX_NEW_PAGE_LINES. Shrinking a legacy
// file is welcome; regrowing beyond its audited low-water mark is not.
export const RATCHETED_SOURCE_LIMITS = new Map([
  ['src/AppShell.jsx', 1047],
  ['src/features/assessment_runtime/AssessmentPageContent.jsx', 1151],
  ['src/features/candidates/CandidateStandingReportPage.jsx', 2252],
  ['src/features/clientintake/ClientIntakePage.jsx', 629],
  ['src/features/dashboard/DashboardPageContent.jsx', 637],
  ['src/features/dev/ButtonShowcasePage.jsx', 684],
  ['src/features/home/HomePage.jsx', 685],
  ['src/features/jobs/JobPipelinePage.jsx', 2505],
  ['src/features/jobs/JobsPage.jsx', 952],
  ['src/features/requisitions/RequisitionsPage.jsx', 1201],
  ['src/features/settings/RecruiterSettingsPage.jsx', 2495],
  ['src/features/settings/RequisitionTemplatePage.jsx', 536],
]);

// These files own global class names. A page-specific stylesheet may extend a
// class through a qualified selector (`.settings-panel .field`), but it must
// not open a new global rule beginning with the canonical class. That prevents
// lazy CSS from silently overriding app-shell/shared styles by import order.
export const CANONICAL_CSS_OWNERS = new Map([
  ['src/styles/08-shared-utilities.css', new Set([
    'field', 'chip', 'filter-chip', 't-pill', 'bar', 'tally-bg-soft',
    'row', 'col', 'grow', 'muted', 'mono', 'display', 'taali-page',
    'taali-page-wide', 'taali-page-compact', 'taali-panel', 'taali-card',
    'taali-page-header',
  ])],
  ['src/styles/23-form-controls.css', new Set([
    'taali-input', 'taali-textarea', 'taali-select', 'taali-select-trigger',
    'taali-select-shell', 'taali-select-shell-inline', 'taali-select-icon',
    'taali-select-value', 'taali-select-value-placeholder',
    'taali-select-chevron', 'taali-select-menu', 'taali-select-option',
    'taali-select-option-selected', 'taali-multiselect-actions',
    'taali-multiselect-action', 'taali-select-trigger-bare',
  ])],
  ['src/features/candidates/BackgroundJobsToaster.css', new Set([
    'bg-jobs-toaster', 'bg-jobs-dismiss', 'bg-jobs-row', 'bg-jobs-icon',
    'bg-jobs-icon-failed', 'bg-jobs-body', 'bg-jobs-title', 'bg-jobs-detail',
    'bg-jobs-bar', 'bg-jobs-bar-fill', 'bg-jobs-cancel', 'bg-jobs-actions',
    'bg-jobs-dismiss-row',
  ])],
]);

const countLines = (content) => {
  if (!content) return 0;
  return content.replace(/\r\n/g, '\n').replace(/\n$/, '').split('\n').length;
};

const walkFiles = (dirPath, predicate, output = []) => {
  if (!fs.existsSync(dirPath)) return output;
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue;
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      walkFiles(fullPath, predicate, output);
    } else if (predicate(fullPath, entry.name)) {
      output.push(fullPath);
    }
  }
  return output;
};

const stripCommentsPreservingLines = (content) => (
  content.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, ' '))
);

const leadingClassOnLine = (line) => {
  const match = line.match(/^\s*\.([A-Za-z_-][\w-]*)(?=[\s,.#:[>{]|$)/);
  return match?.[1] || null;
};

const findCssOwnershipViolations = ({ projectRoot, srcRoot }) => {
  const violations = [];
  const ownerClasses = new Map();

  for (const [ownerRelativePath, classes] of CANONICAL_CSS_OWNERS) {
    for (const className of classes) {
      ownerClasses.set(className, ownerRelativePath);
    }
  }

  const cssFiles = walkFiles(srcRoot, (fullPath) => path.extname(fullPath) === '.css');
  for (const cssPath of cssFiles) {
    const relativePath = path.relative(projectRoot, cssPath).split(path.sep).join('/');
    const content = stripCommentsPreservingLines(fs.readFileSync(cssPath, 'utf8'));
    content.split('\n').forEach((line, index) => {
      const className = leadingClassOnLine(line);
      const owner = className ? ownerClasses.get(className) : null;
      if (!owner || owner === relativePath) return;
      violations.push(
        `Global .${className} CSS must stay in ${owner}; `
        + `${relativePath}:${index + 1} redefines it. Use a page-qualified selector instead.`,
      );
    });
  }

  return violations;
};

export const findArchitectureViolations = ({ projectRoot = DEFAULT_PROJECT_ROOT } = {}) => {
  const srcRoot = path.join(projectRoot, 'src');
  const featureRoot = path.join(srcRoot, 'features');
  const violations = [];

  const sourceFiles = walkFiles(
    srcRoot,
    (fullPath) => SOURCE_EXTENSIONS.has(path.extname(fullPath)),
  );
  for (const fullPath of sourceFiles) {
    const content = fs.readFileSync(fullPath, 'utf8');
    for (const pattern of DISALLOWED_IMPORT_PATTERNS) {
      if (pattern.test(content)) {
        violations.push(
          `Disallowed legacy API import in ${path.relative(projectRoot, fullPath)} (matched ${pattern}).`,
        );
        break;
      }
    }
  }

  const appEntryPath = path.join(srcRoot, 'App.jsx');
  if (fs.existsSync(appEntryPath)) {
    const appContent = fs.readFileSync(appEntryPath, 'utf8');
    const appLines = countLines(appContent);
    if (appLines > APP_ENTRY_MAX_LINES) {
      violations.push(
        `App entry too large: src/App.jsx has ${appLines} lines (max ${APP_ENTRY_MAX_LINES}).`,
      );
    }
  }

  for (const [relativePath, maxLines] of RATCHETED_SOURCE_LIMITS) {
    const fullPath = path.join(projectRoot, relativePath);
    if (!fs.existsSync(fullPath)) continue;
    const content = fs.readFileSync(fullPath, 'utf8');
    const lines = countLines(content);
    if (lines > maxLines) {
      violations.push(
        `Ratcheted hotspot grew: ${relativePath} has ${lines} lines (max ${maxLines}).`,
      );
    }
  }

  // Hash routing can hide in the real AppShell behind src/App.jsx's re-export.
  for (const relativePath of ['src/App.jsx', 'src/AppShell.jsx']) {
    const fullPath = path.join(projectRoot, relativePath);
    if (!fs.existsSync(fullPath)) continue;
    const content = fs.readFileSync(fullPath, 'utf8');
    if (HASH_ROUTE_FALLBACK_PATTERNS.some((pattern) => pattern.test(content))) {
      violations.push(`Hash-route compatibility fallback detected in ${relativePath}.`);
    }
  }

  const pageFiles = walkFiles(featureRoot, (_fullPath, fileName) => PAGE_FILE_PATTERN.test(fileName));
  for (const fullPath of pageFiles) {
    const relativePath = path.relative(projectRoot, fullPath).split(path.sep).join('/');
    if (RATCHETED_SOURCE_LIMITS.has(relativePath)) continue;
    const lines = countLines(fs.readFileSync(fullPath, 'utf8'));
    if (lines > MAX_NEW_PAGE_LINES) {
      violations.push(
        `Feature page too large: ${relativePath} has ${lines} lines `
        + `(max ${MAX_NEW_PAGE_LINES}; add no new oversized-page baselines).`,
      );
    }
  }

  violations.push(...findCssOwnershipViolations({ projectRoot, srcRoot }));
  return violations;
};

export const main = ({ projectRoot = DEFAULT_PROJECT_ROOT } = {}) => {
  const violations = findArchitectureViolations({ projectRoot });
  if (violations.length > 0) {
    console.error('Frontend architecture gate failed:');
    for (const violation of violations) console.error(`- ${violation}`);
    return 1;
  }
  console.log('Frontend architecture gate passed.');
  return 0;
};

const isDirectRun = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isDirectRun) process.exitCode = main();
