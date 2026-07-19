// @vitest-environment node

import { describe, expect, it } from 'vitest';

import { findWarningDiagnostics, warningGateExitCode } from './run-vitest-clean.mjs';

describe('Vitest warning gate', () => {
  it('detects the warning families that must keep CI red', () => {
    const output = [
      '\u001B[33mstderr | src/example.test.jsx > renders safely\u001B[0m',
      'Warning: An update to Example inside a test was not wrapped in act(...).',
      '⚠️ React Router Future Flag Warning: enable v7_startTransition.',
      'You have Reduced Motion enabled on your device. Animations may not appear as expected.',
      'Warning: ReactDOM.render is no longer supported in React 18.',
    ].join('\n');

    expect(findWarningDiagnostics(output).map(({ kind }) => kind)).toEqual([
      'vitest-stderr',
      'react-act',
      'react-router-future-flag',
      'motion-reduced-motion',
      'react-warning',
    ]);
  });

  it('does not treat ordinary stdout/stderr words or stdout headers as warnings', () => {
    const output = [
      '✓ AssessmentPage > succeeds without stdout or stderr',
      'stdout | src/example.test.jsx > logs a useful message',
      'Test Files  1 passed (1)',
      'Tests  3 passed (3)',
    ].join('\n');

    expect(findWarningDiagnostics(output)).toEqual([]);
  });

  it('fails a warning-only clean run without replacing Vitest failures', () => {
    const warning = [{ kind: 'react-warning', line: 'Warning: example' }];

    expect(warningGateExitCode(0, [])).toBe(0);
    expect(warningGateExitCode(0, warning)).toBe(1);
    expect(warningGateExitCode(2, warning)).toBe(2);
    expect(warningGateExitCode(7, [])).toBe(7);
    expect(warningGateExitCode(null, [])).toBe(1);
  });
});
