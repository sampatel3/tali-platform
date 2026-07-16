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
