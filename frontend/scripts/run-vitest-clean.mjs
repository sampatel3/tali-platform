import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const ANSI_ESCAPE = new RegExp(
  `${String.fromCharCode(27)}\\[[0-?]*[ -/]*[@-~]`,
  'g',
);

const WARNING_PATTERNS = [
  {
    kind: 'react-act',
    pattern: /^\s*(?:Warning:\s*)?An update to .+ inside a test was not wrapped in act\(\.\.\.\)\./,
  },
  {
    kind: 'react-router-future-flag',
    pattern: /^\s*(?:⚠️\s*)?React Router Future Flag Warning:/,
  },
  {
    kind: 'motion-reduced-motion',
    pattern: /^\s*You have Reduced Motion enabled on your device\./,
  },
  {
    // Vitest prints this structured header when a test writes to stderr. Keep
    // it anchored so a test name such as "renders stdout/stderr" stays valid.
    kind: 'vitest-stderr',
    pattern: /^\s*stderr\s+\|(?:\s|$)/,
  },
  {
    kind: 'react-warning',
    pattern: /^\s*Warning:\s+\S/,
  },
];

export function findWarningDiagnostics(output) {
  const cleanOutput = String(output)
    .replace(ANSI_ESCAPE, '')
    .replaceAll('\r\n', '\n')
    .replaceAll('\r', '\n');
  const diagnostics = [];

  for (const [index, line] of cleanOutput.split('\n').entries()) {
    const match = WARNING_PATTERNS.find(({ pattern }) => pattern.test(line));
    if (match) diagnostics.push({ kind: match.kind, line: line.trim(), lineNumber: index + 1 });
  }

  return diagnostics;
}

export function warningGateExitCode(vitestExitCode, diagnostics) {
  if (!Number.isInteger(vitestExitCode) || vitestExitCode < 0) return 1;
  return vitestExitCode === 0 && diagnostics.length > 0 ? 1 : vitestExitCode;
}

async function runVitest(args) {
  const vitestEntry = fileURLToPath(new URL('../node_modules/vitest/vitest.mjs', import.meta.url));
  const child = spawn(process.execPath, [vitestEntry, 'run', ...args], {
    cwd: process.cwd(),
    env: process.env,
    stdio: ['inherit', 'pipe', 'pipe'],
  });
  const stdout = [];
  const stderr = [];

  child.stdout.on('data', (chunk) => {
    stdout.push(chunk);
    process.stdout.write(chunk);
  });
  child.stderr.on('data', (chunk) => {
    stderr.push(chunk);
    process.stderr.write(chunk);
  });

  const result = await new Promise((resolveResult, reject) => {
    child.once('error', reject);
    child.once('close', (exitCode, signal) => resolveResult({ exitCode, signal }));
  });

  return {
    ...result,
    diagnostics: [
      ...findWarningDiagnostics(Buffer.concat(stdout).toString('utf8')),
      ...findWarningDiagnostics(Buffer.concat(stderr).toString('utf8')),
    ],
  };
}

function reportDiagnostics(diagnostics) {
  const unique = [...new Map(diagnostics.map((item) => [`${item.kind}:${item.line}`, item])).values()];
  process.stderr.write(
    `\n[frontend-warning-gate] Found ${diagnostics.length} warning diagnostic(s); `
      + 'fix their source before merging.\n',
  );
  for (const diagnostic of unique.slice(0, 20)) {
    process.stderr.write(`  - ${diagnostic.kind}: ${diagnostic.line}\n`);
  }
  if (unique.length > 20) {
    process.stderr.write(`  - … ${unique.length - 20} additional unique diagnostic(s)\n`);
  }
}

async function main() {
  const { exitCode, signal, diagnostics } = await runVitest(process.argv.slice(2));
  if (diagnostics.length > 0) reportDiagnostics(diagnostics);

  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exitCode = warningGateExitCode(exitCode, diagnostics);
}

const invokedAsScript = process.argv[1]
  && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;

if (invokedAsScript) {
  main().catch((error) => {
    process.stderr.write(`[frontend-warning-gate] ${error.stack || error}\n`);
    process.exitCode = 1;
  });
}
