import { describe, expect, it } from 'vitest';

import * as canonical from './jobPipelineUtils';
import * as legacy from './jobPipelinePageUtils';

const HISTORICAL_EXPORTS = [
  'PIPELINE_STAGE_ORDER',
  'matchesPipelineStage',
  'normalizeThreshold',
  'formatRelativeShort',
  'buildApplicationTitle',
  'resolveOptionalPercent',
  'formatStageLabel',
  'GRANULAR_AUTOMATION_KEYS',
  'resolvedRoleAutomation',
  'resolvedDeterministicReject',
  'activationAutonomyPayload',
  'formatDecisionLabel',
];

describe('jobPipelinePageUtils compatibility facade', () => {
  it('retains every historical export', () => {
    expect(Object.keys(legacy)).toEqual(expect.arrayContaining(HISTORICAL_EXPORTS));
  });

  it('forwards every canonical named export without copying implementations', () => {
    const canonicalNames = Object.keys(canonical)
      .filter((name) => name !== 'default')
      .sort();

    expect(Object.keys(legacy).sort()).toEqual(canonicalNames);
    for (const name of canonicalNames) {
      expect(legacy[name]).toBe(canonical[name]);
    }
  });
});

describe('legacy role autonomy compatibility', () => {
  it('reads the effective legacy policy and omits unsaved fields during Turn on', () => {
    const role = {
      auto_promote: true,
      auto_send_assessment: null,
      auto_resend_assessment: null,
      auto_advance: null,
      agent_effective_policy: {
        auto_send_assessment: true,
        auto_resend_assessment: true,
        auto_advance: true,
        auto_skip_assessment: true,
      },
    };

    expect(canonical.resolvedRoleAutomation(role, 'auto_send_assessment')).toBe(true);
    expect(canonical.resolvedRoleAutomation(role, 'auto_resend_assessment')).toBe(true);
    expect(canonical.resolvedRoleAutomation(role, 'auto_advance')).toBe(true);
    expect(canonical.resolvedRoleAutoSkipAssessment(role)).toBe(true);
    expect(canonical.activationAutonomyPayload(role)).toEqual({});
  });

  it('counts only explicitly active assessment tasks as activation-ready', () => {
    expect(canonical.hasActiveAssessmentTask([
      { id: 1, is_active: false },
      { id: 2 },
    ])).toBe(false);
    expect(canonical.hasActiveAssessmentTask([
      { id: 1, is_active: false },
      { id: 2, is_active: true },
    ])).toBe(true);
  });
});
