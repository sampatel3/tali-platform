import { describe, expect, it } from 'vitest';

import {
  AI_SHOWCASE_COMPLETED_ASSESSMENT,
  PRODUCT_WALKTHROUGH,
} from './productWalkthroughModels';

// The marketing showcase embeds the live candidate-report components with this
// mock. Two cv_match requirement schemas are live in prod (legacy
// requirement/evidence/impact and the newer criterion_text/cv_quote/must_have),
// so the mock must mirror that mix — otherwise the showcase quietly stops
// exercising the normalization the real report depends on.
describe('product walkthrough showcase mock', () => {
  const rawRows = AI_SHOWCASE_COMPLETED_ASSESSMENT.cv_job_match_details.requirements_assessment;

  it('carries BOTH live requirement schemas (legacy + cv_match_v4)', () => {
    const hasV4 = rawRows.some((r) => r.criterion_text && r.requirement === undefined);
    const hasLegacy = rawRows.some((r) => r.requirement && r.criterion_text === undefined);
    expect(hasV4).toBe(true);
    expect(hasLegacy).toBe(true);
  });

  it('renders every requirement row through buildStandingCandidateReportModel', () => {
    const reqs = PRODUCT_WALKTHROUGH.report.reportModel.roleFitModel.requirementsAssessment;
    // No row drops out, and none has an empty/undefined requirement label —
    // the exact failure mode (undefined requirement) that blanked the page.
    expect(reqs.length).toBe(rawRows.length);
    for (const row of reqs) {
      expect(typeof row.requirement).toBe('string');
      expect(row.requirement.length).toBeGreaterThan(0);
    }
    // The v4 row's criterion_text surfaced as the requirement label.
    expect(reqs.map((r) => r.requirement)).toContain(
      'Owned a production GenAI release in a regulated domain',
    );
  });
});
