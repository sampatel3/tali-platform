import fs from 'node:fs';
import path from 'node:path';

const projectRoot = path.resolve(process.cwd());
const srcRoot = path.join(projectRoot, 'src');
const appShellPath = path.join(srcRoot, 'App.jsx');
const featureRoot = path.join(srcRoot, 'features');

const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx']);
// Matches `*Page.jsx` AND `*PageContent.jsx` — the latter so a page can't
// dodge the cap by renaming its body to `…PageContent.jsx` behind a one-line
// `…Page.jsx` re-export (which is exactly how CandidatesDirectory grew).
const PAGE_FILE_PATTERN = /Page(Content)?\.(js|jsx|ts|tsx)$/;
// Hard cap on `*Page.jsx` line counts. The v3 Mission Control redesign
// pushed several pages well past the original 500-line gate because the
// canvas hero / dimension grids / evidence cards live inline. The cap
// here accommodates the redesign reality (RecruiterSettingsPage +
// CandidateStandingReportPage still sit in the 2k–2.5k range). The gate's
// job is to catch *new* bloat past the post-redesign baseline.
//
// History: nudged 2625→2650 for the agent-settings save/toggle race fixes
// in JobPipelinePage; 2650→2660 for the post-#278 baseline (global
// interview anchor / dynamic threshold UX); then bumped 2660→2700 when
// PRs #538 + #541 (archived-Workable-job handling) both edited
// JobPipelinePage off the same parent and landed it at 2691 — the bump
// instead of a split left main CI red. JobPipelinePage has now been split
// (the job-spec parser/formatter → jobSpecFormatting.jsx and the Agent
// settings tab → RoleAgentSettingsTab.jsx), dropping it to ~1.7k, so the
// cap is restored to 2660 to keep the gate meaningful. Bumped 2660→2680
// after #734 (integrity-aware scoring) + #737 grew CandidateStandingReportPage
// to ~2679 inline, reddening main for every PR — the same bump-now-split-later
// as #538/#541 above. CandidateStandingReportPage has now been split as well
// (the CV document viewer → CvDocumentViewer.jsx, the CV-match + integrity
// readout → CvMatchReview.jsx, and the interview-prep card →
// PrepQuestionCard.jsx), dropping it to ~1.9k, so the cap is restored to 2660.
const MAX_PAGE_LINES = 2660;
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
  if (appLines > 500) {
    violations.push(`App shell too large: src/App.jsx has ${appLines} lines (max 500).`);
  }
  if (appContent.includes('location.hash') || appContent.includes('window.location.hash')) {
    violations.push('Hash-route compatibility fallback detected in src/App.jsx.');
  }
}

if (fs.existsSync(featureRoot)) {
  const featureEntries = fs.readdirSync(featureRoot, { withFileTypes: true });
  for (const entry of featureEntries) {
    if (!entry.isDirectory()) continue;
    const featureDir = path.join(featureRoot, entry.name);
    const files = fs.readdirSync(featureDir, { withFileTypes: true });
    for (const file of files) {
      if (!file.isFile()) continue;
      if (!PAGE_FILE_PATTERN.test(file.name)) continue;
      const fullPath = path.join(featureDir, file.name);
      const lines = fs.readFileSync(fullPath, 'utf8').split('\n').length;
      if (lines > MAX_PAGE_LINES) {
        violations.push(
          `Feature page too large: ${path.relative(projectRoot, fullPath)} has ${lines} lines (max ${MAX_PAGE_LINES}).`
        );
      }
    }
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
