#!/usr/bin/env node
/**
 * Guardrail script: enforces the TAALI token-era UI policy.
 * Run: npm run lint:ui
 *
 * Fails on:
 * - undefined CSS variables
 * - new raw border-2 surface styling outside the migration allowlist
 * - new hardcoded hex colors in component code outside the migration allowlist
 * - new raw white/gray/black utility styling where semantic tokens should be used
 * - alternate theme-toggle implementations outside the shared toggle primitives
 * - gradient tokens passed through bg-[var(...)] utility misuse
 * - square table-shell treatments outside the migration allowlist
 */

import { readdir, readFile } from 'fs/promises';
import { join, relative } from 'path';
import { fileURLToPath } from 'url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const ROOT = join(__dirname, '..');

const IGNORE_DIR_NAMES = new Set(['node_modules', '.git', 'dist', 'coverage']);
const FILE_PATTERN = /\.(jsx?|tsx?|css|mjs)$/;

const BORDER2_ALLOWLIST = [
  'src/components/assessment/ClaudeChat.jsx',
  'src/context/ToastContext.jsx',
  'src/shared/ui/ErrorBoundary.jsx',
  'src/features/tasks/CreateTaskModal.jsx',
  'src/features/tasks/TasksListView.jsx',
  'src/features/candidates/CandidatesPage.jsx',
  'src/features/candidates/CandidatesPageContent.jsx',
  'src/features/candidates/RolesList.jsx',
  'src/features/settings/SettingsPage.jsx',
  'src/features/settings/SettingsPageContent.jsx',
  'src/features/candidates/CandidateSheet.jsx',
  'src/features/integrations/WorkableConnection.jsx',
  'src/features/auth/RegisterPage.jsx',
  'src/features/candidates/RoleSheet.jsx',
  'src/features/demo/DemoExperiencePage.jsx',
  'src/features/auth/VerifyEmailPage.jsx',
  'src/features/auth/LoginPage.jsx',
  'src/features/auth/ForgotPasswordPage.jsx',
  'src/features/auth/ResetPasswordPage.jsx',
];

const HEX_ALLOWLIST = [
  'src/App.jsx',
  'src/AppShell.jsx',
  'src/features/assessment_runtime/AssessmentBrandGlyph.jsx',
  'src/features/assessment_runtime/AssessmentTerminal.jsx',
  'src/features/assessment_runtime/CandidateFeedbackPage.jsx',
  'src/features/assessment_runtime/CandidateWelcomePage.jsx',
  'src/features/assessment_runtime/DemoAssessmentSummary.jsx',
  'src/features/candidates/CandidateDetailPage.jsx',
  'src/features/candidates/CandidateDetailPageContent.jsx',
  'src/features/candidates/CandidateDetailSecondaryTabs.jsx',
  'src/features/candidates/CandidateSidebarHeader.jsx',
  'src/features/candidates/CandidatesTable.jsx',
  'src/features/dashboard/DashboardNav.jsx',
  'src/features/integrations/WorkableConnection.jsx',
  'src/features/marketing/LandingPage.jsx',
  'src/features/marketing/LandingPageContent.jsx',
  'src/shared/ui/Branding.jsx',
  'src/shared/ui/ComparisonRadar.jsx',
];

const RAW_UTILITY_ALLOWLIST = [
  'src/components/assessment/CodeEditor.jsx',
  'src/components/assessment/ClaudeChat.jsx',
  'src/features/assessment_runtime/CandidateWelcomePage.jsx',
  'src/features/assessment_runtime/AssessmentWorkspace.jsx',
  'src/features/assessment_runtime/CandidateFeedbackPage.jsx',
  'src/features/assessment_runtime/AssessmentPage.jsx',
  'src/features/assessment_runtime/AssessmentPageContent.jsx',
  'src/index.css',
  'src/features/assessment_runtime/AssessmentTopBar.jsx',
  'src/features/integrations/WorkableConnection.jsx',
  'src/features/settings/SettingsPage.jsx',
  'src/features/settings/SettingsPageContent.jsx',
  'src/features/dashboard/DashboardNav.jsx',
  'src/features/candidates/AssessmentInviteSheet.jsx',
  'src/features/candidates/RoleSummaryHeader.jsx',
  'src/features/candidates/CandidateSidebarHeader.jsx',
  'src/features/candidates/SearchInput.jsx',
  'src/features/candidates/CandidateSheet.jsx',
  'src/features/candidates/RoleSheet.jsx',
  'src/features/candidates/CandidateDetailSecondaryTabs.jsx',
  'src/features/candidates/CandidateEvaluateTab.jsx',
  'src/features/tasks/CreateTaskModal.jsx',
  'src/features/candidates/CandidatesTable.jsx',
  'src/features/candidates/CandidateDetailPage.jsx',
  'src/features/candidates/CandidateDetailPageContent.jsx',
  'src/features/candidates/RolesList.jsx',
  'src/features/demo/DemoExperiencePage.jsx',
  'src/features/tasks/TasksListView.jsx',
];

const THEME_TOGGLE_ALLOWLIST = [
  'src/shared/ui/ThemeModeToggle.jsx',
  'src/shared/ui/GlobalThemeToggle.jsx',
];

const SQUARE_TABLE_ALLOWLIST = [
  'src/features/candidates/CandidateDetailSecondaryTabs.jsx',
];

const COMPONENT_FILE_PATTERN = /\.(jsx?|tsx?)$/;
const TEST_FILE_PATTERN = /\.test\.(jsx?|tsx?)$/;

const BORDER2_PATTERN = /\bborder-2\b/g;
const HEX_PATTERN = /#(?:[0-9a-fA-F]{3,8})\b/g;
const RAW_UTILITY_PATTERN = /\b(?:bg|text|border)-(?:white|black|gray(?:-\d{2,3})?)(?:\/\d{1,3})?\b/g;
const THEME_TOGGLE_PATTERN = /\b(?:Switch to light theme|Switch to dark theme|Light UI|Dark UI)\b/g;
const GRADIENT_BG_VAR_PATTERN = /\bbg-\[var\(--[^)\]]*gradient[^)\]]*\)\]/g;
const SQUARE_TABLE_PATTERN = /\b(?:rounded-none|rounded-sm)\b/g;
const CSS_VAR_USAGE_PATTERN = /var\(\s*(--[\w-]+)\s*(?:,[^)]+)?\)/g;
const CSS_VAR_DEFINITION_PATTERN = /(--[\w-]+)\s*:/g;

const isIgnored = (fullPath) => fullPath.split('/').some((segment) => IGNORE_DIR_NAMES.has(segment));
const isAllowlisted = (relPath, allowlist) => allowlist.some((entry) => relPath === entry || relPath.startsWith(`${entry}/`));

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

const lineNumberForIndex = (content, index) => content.slice(0, index).split('\n').length;

async function main() {
  const files = await walk(ROOT);
  const cssFiles = files.filter((relPath) => relPath.endsWith('.css'));
  const definedCssVars = new Set();

  for (const relPath of cssFiles) {
    const content = await readFile(join(ROOT, relPath), 'utf-8');
    let match;
    while ((match = CSS_VAR_DEFINITION_PATTERN.exec(content)) !== null) {
      definedCssVars.add(match[1]);
    }
  }

  const errors = [];

  for (const relPath of files) {
    const content = await readFile(join(ROOT, relPath), 'utf-8');

    let match;
    const cssVarPattern = new RegExp(CSS_VAR_USAGE_PATTERN.source, CSS_VAR_USAGE_PATTERN.flags);
    while ((match = cssVarPattern.exec(content)) !== null) {
      const variableName = match[1];
      if (definedCssVars.has(variableName)) continue;
      errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — undefined CSS variable "${variableName}"`);
    }

    if (!COMPONENT_FILE_PATTERN.test(relPath)) {
      continue;
    }

    const isTestFile = TEST_FILE_PATTERN.test(relPath);

    const borderPattern = new RegExp(BORDER2_PATTERN.source, BORDER2_PATTERN.flags);
    while ((match = borderPattern.exec(content)) !== null) {
      if (isAllowlisted(relPath, BORDER2_ALLOWLIST)) continue;
      errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — raw border-2 surface styling is not allowed`);
    }

    const hexPattern = new RegExp(HEX_PATTERN.source, HEX_PATTERN.flags);
    while ((match = hexPattern.exec(content)) !== null) {
      if (isAllowlisted(relPath, HEX_ALLOWLIST)) continue;
      errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — hardcoded hex color "${match[0]}" is not allowed`);
    }

    const rawUtilityPattern = new RegExp(RAW_UTILITY_PATTERN.source, RAW_UTILITY_PATTERN.flags);
    while ((match = rawUtilityPattern.exec(content)) !== null) {
      if (isAllowlisted(relPath, RAW_UTILITY_ALLOWLIST)) continue;
      errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — raw ${match[0]} utility should be replaced with semantic tokens`);
    }

    if (isTestFile) {
      continue;
    }

    const themeTogglePattern = new RegExp(THEME_TOGGLE_PATTERN.source, THEME_TOGGLE_PATTERN.flags);
    while ((match = themeTogglePattern.exec(content)) !== null) {
      if (isAllowlisted(relPath, THEME_TOGGLE_ALLOWLIST)) continue;
      errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — theme toggle text should only live in the shared toggle primitives`);
    }

    const gradientBgVarPattern = new RegExp(GRADIENT_BG_VAR_PATTERN.source, GRADIENT_BG_VAR_PATTERN.flags);
    while ((match = gradientBgVarPattern.exec(content)) !== null) {
      errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — gradient tokens should not be passed through bg-[var(...)] utilities`);
    }

    if (content.includes('TableShell') || content.includes('<table') || content.includes('overflow-x-auto')) {
      const squareTablePattern = new RegExp(SQUARE_TABLE_PATTERN.source, SQUARE_TABLE_PATTERN.flags);
      while ((match = squareTablePattern.exec(content)) !== null) {
        if (isAllowlisted(relPath, SQUARE_TABLE_ALLOWLIST)) continue;
        errors.push(`${relPath}:${lineNumberForIndex(content, match.index)} — square table-shell rounding is not allowed`);
      }
    }
  }

  if (errors.length > 0) {
    console.error('lint:ui failed — token guardrail violations:\n');
    errors.slice(0, 80).forEach((error) => console.error(`  ${error}`));
    if (errors.length > 80) {
      console.error(`  ... and ${errors.length - 80} more`);
    }
    process.exit(1);
  }

  console.log('lint:ui: OK');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
