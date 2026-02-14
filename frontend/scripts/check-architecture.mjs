import fs from 'node:fs';
import path from 'node:path';

const projectRoot = path.resolve(process.cwd());
const srcRoot = path.join(projectRoot, 'src');
const appShellPath = path.join(srcRoot, 'App.jsx');

const SOURCE_EXTENSIONS = new Set(['.js', '.jsx', '.ts', '.tsx']);
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

if (violations.length > 0) {
  console.error('Frontend architecture gate failed:');
  for (const violation of violations) {
    console.error(`- ${violation}`);
  }
  process.exit(1);
}

console.log('Frontend architecture gate passed.');
