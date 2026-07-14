import { describe, expect, it } from 'vitest';

import { applicationFunnelBucket, isPostHandoverWorkableStage } from './metrics';

describe('applicationFunnelBucket', () => {
  it('does not fold a Workable post-handover stage into the Advanced evaluation bucket', () => {
    const application = {
      pipeline_stage: 'applied',
      application_outcome: 'open',
      workable_stage: 'offer_extended',
      external_stage_raw: 'offer_extended',
      hiring_stage_context: {
        stage: 'offer',
        provider: 'workable',
      },
    };

    // The external stage remains useful context, but it is a separate axis.
    expect(isPostHandoverWorkableStage(application.workable_stage)).toBe(true);
    expect(applicationFunnelBucket(application)).toBe('applied');
  });

  it('uses Advanced only when the explicit Tali evaluation stage is advanced', () => {
    expect(applicationFunnelBucket({
      pipeline_stage: 'advanced',
      hiring_stage_context: { stage: 'screening', provider: 'native' },
    })).toBe('advanced');
  });
});
