import fs from 'node:fs';
import path from 'node:path';

const projectRoot = path.resolve(process.cwd());
const srcRoot = path.join(projectRoot, 'src');
const appShellPath = path.join(srcRoot, 'AppShell.jsx');
const featureRoot = path.join(srcRoot, 'features');

const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx']);
// Matches `*Page.jsx` AND `*PageContent.jsx` — the latter so a page can't
// dodge the cap by renaming its body to `…PageContent.jsx` behind a one-line
// `…Page.jsx` re-export (which is exactly how CandidatesDirectory grew).
const PAGE_FILE_PATTERN = /Page(Content)?\.(js|jsx|ts|tsx)$/;
// New pages and the eventual split children of legacy pages must stay small.
// Existing oversized pages are frozen at an exact baseline below. Requiring
// the baseline to be lowered whenever a file shrinks makes this a ratchet:
// a later change cannot quietly spend the lines that a refactor removed.
const MAX_NEW_PAGE_LINES = 500;
const APP_SHELL_BASELINE = 1168;
const OVERSIZED_PAGE_BASELINES = new Map([
  ['src/features/assessment_runtime/AssessmentPageContent.jsx', 1152],
  ['src/features/candidates/CandidateStandingReportPage.jsx', 2263],
  ['src/features/clientintake/ClientIntakePage.jsx', 630],
  ['src/features/dashboard/DashboardPageContent.jsx', 650],
  ['src/features/dev/ButtonShowcasePage.jsx', 685],
  ['src/features/home/HomePage.jsx', 686],
  ['src/features/jobs/JobPipelinePage.jsx', 2621],
  ['src/features/jobs/JobsPage.jsx', 963],
  ['src/features/requisitions/RequisitionsPage.jsx', 1202],
  ['src/features/settings/RecruiterSettingsPage.jsx', 2496],
  ['src/features/settings/RequisitionTemplatePage.jsx', 537],
]);
const DISALLOWED_IMPORT_PATTERNS = [
  /from\s+['"][^'"]*lib\/api(?:\.js)?['"]/g,
  /import\s*\(\s*['"][^'"]*lib\/api(?:\.js)?['"]\s*\)/g,
];

const violations = [];

const walk = (dirPath) => {
  const entries = fs.readdirSync(dirPath, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue;
    const fullPath = path.join(dirPath, entry.name);
    if (entry.isDirectory()) {
      walk(fullPath);
      continue;
    }
    const ext = path.extname(entry.name);
    if (!SOURCE_EXTENSIONS.has(ext)) continue;
    const content = fs.readFileSync(fullPath, 'utf8');
    for (const pattern of DISALLOWED_IMPORT_PATTERNS) {
      if (pattern.test(content)) {
        violations.push(
          `Disallowed legacy API import in ${path.relative(projectRoot, fullPath)} (matched ${pattern}).`
        );
        break;
      }
    }
  }
};

walk(srcRoot);

if (fs.existsSync(appShellPath)) {
  const appContent = fs.readFileSync(appShellPath, 'utf8');
  const appLines = appContent.split('\n').length;
  if (appLines !== APP_SHELL_BASELINE) {
    const direction = appLines > APP_SHELL_BASELINE ? 'grew' : 'shrunk';
    violations.push(
      `App shell ${direction}: src/AppShell.jsx has ${appLines} lines ` +
      `(ratchet baseline ${APP_SHELL_BASELINE}). Split growth; lower the baseline after shrinkage.`
    );
  }
  // Anchors and preserving a hash in a post-login `next` URL are legitimate.
  // These patterns identify an actual second hash-based router.
  if (
    appContent.includes('<HashRouter')
    || appContent.includes("addEventListener('hashchange'")
    || appContent.includes('addEventListener("hashchange"')
  ) {
    violations.push('Hash-route compatibility router detected in src/AppShell.jsx.');
  }
}

if (fs.existsSync(featureRoot)) {
  const checkPages = (directory) => {
    const files = fs.readdirSync(directory, { withFileTypes: true });
    for (const file of files) {
      const fullPath = path.join(directory, file.name);
      if (file.isDirectory()) {
        checkPages(fullPath);
        continue;
      }
      if (!file.isFile() || !PAGE_FILE_PATTERN.test(file.name)) continue;
      const lines = fs.readFileSync(fullPath, 'utf8').split('\n').length;
      const relativePath = path.relative(projectRoot, fullPath);
      const baseline = OVERSIZED_PAGE_BASELINES.get(relativePath);
      if (baseline !== undefined && lines !== baseline) {
        const direction = lines > baseline ? 'grew' : 'shrunk';
        violations.push(
          `Legacy page ${direction}: ${relativePath} has ${lines} lines ` +
          `(ratchet baseline ${baseline}). Split growth; lower the baseline after shrinkage.`
        );
      } else if (baseline === undefined && lines > MAX_NEW_PAGE_LINES) {
        violations.push(
          `Feature page too large: ${relativePath} has ${lines} lines ` +
          `(max ${MAX_NEW_PAGE_LINES}; add no new oversized-page baselines).`
        );
      }
    }
  };
  checkPages(featureRoot);
}

for (const [relativePath] of OVERSIZED_PAGE_BASELINES) {
  if (!fs.existsSync(path.join(projectRoot, relativePath))) {
    violations.push(`Stale oversized-page baseline for missing file: ${relativePath}.`);
  }
}

if (violations.length > 0) {
  console.error('Frontend architecture gate failed:');
  for (const violation of violations) {
    console.error(`- ${violation}`);
  }
  process.exit(1);
}

console.log('Frontend architecture gate passed.');
