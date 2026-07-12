import { render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

import { HomeAnalyticsSummary } from './HomeAnalyticsSummary';

// The pulse values use the shared MotionNumber once their live value settles. This
// pins the two things the motion pass added: the entrance-reveal class on the
// section, and the reduced-motion contract — a reduced-motion viewer must see
// the FINAL numbers immediately (no 0-flash, nothing left animating/hidden).

const mockReducedMotion = (reduced) => {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches: reduced,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
};

const kpis = {
  today: 12,
  auto_applied_today: 3,
  override_rate_pct: 40,
  teach_rate_pct: 25,
  org_budget_spent_cents: 700,
};
const orgBudget = { unit: '/ $50' };

beforeEach(() => vi.clearAllMocks());
afterEach(() => { delete window.matchMedia; });

test('renders the pulse section with the entrance-reveal class', () => {
  mockReducedMotion(true);
  const { container } = render(<HomeAnalyticsSummary kpis={kpis} orgBudget={orgBudget} />);
  const section = container.querySelector('section.home-pulse');
  expect(section).not.toBeNull();
  expect(section.className).toContain('home-section');
  expect(section.className).toContain('reveal');
});

test('under reduced motion the pulse numbers render their final value immediately', () => {
  mockReducedMotion(true);
  const { container } = render(<HomeAnalyticsSummary kpis={kpis} orgBudget={orgBudget} />);
  const values = [...container.querySelectorAll('.home-pulse-v')].map((n) => n.textContent);
  expect(values).toHaveLength(5);
  expect(values[0]).toBe('12');
  expect(values[1]).toBe('3');
  expect(values[2]).toBe('40%');
  expect(values[3]).toBe('25%');
  // Spend cell renders the final dollar amount + its unit — never a 0-flash.
  expect(values[4]).toContain('$7');
  expect(values[4]).toContain('/ $50');
});
