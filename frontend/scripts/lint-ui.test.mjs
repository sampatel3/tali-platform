// @vitest-environment node

import { describe, expect, it } from 'vitest';

import {
  collectApplicationViolations,
  collectCssVariableViolations,
  findCssVariableDefinitions,
} from './lint-ui.mjs';

describe('lint-ui guardrail', () => {
  it('accepts literal fallbacks and checks unresolved nested fallbacks', () => {
    const content = [
      '.safe { color: var(--optional, #fff); }',
      '.unsafe { color: var(--optional, var(--required)); }',
    ].join('\n');

    const violations = collectCssVariableViolations('src/example.css', content, new Set());

    expect(violations).toHaveLength(1);
    expect(violations[0]).toMatchObject({ value: '--required', line: 2 });
  });

  it('recognises CSS and React inline custom-property definitions', () => {
    const definitions = findCssVariableDefinitions(`
      .root { --from-css: red; }
      const style = { '--from-react': value };
      element.style.setProperty('--from-js', value);
    `);

    expect(definitions).toEqual(new Set(['--from-css', '--from-react', '--from-js']));
  });

  it('reports a raw token violation without an allowlist', () => {
    const findings = collectApplicationViolations(
      'src/features/example/NewCard.jsx',
      '<div className="text-white">New card</div>',
    );

    expect(findings).toEqual([
      expect.objectContaining({
        rule: 'raw-color-utility',
        value: 'text-white',
      }),
    ]);
  });
});
