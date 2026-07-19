import { describe, expect, it, vi } from 'vitest';

import { loadDecisionWorkableStages } from './loadDecisionWorkableStages';

describe('loadDecisionWorkableStages', () => {
  it('does not call Workable for an internal or Bullhorn advance', async () => {
    const getWorkableStages = vi.fn();
    await expect(loadDecisionWorkableStages(
      { getWorkableStages },
      { workable_job_id: null },
      { requireStagePick: true },
    )).resolves.toEqual([]);
    expect(getWorkableStages).not.toHaveBeenCalled();
  });

  it('loads the exact linked Workable job stages', async () => {
    const stages = [{ slug: 'phone-screen', name: 'Phone screen' }];
    const getWorkableStages = vi.fn().mockResolvedValue({ data: { stages } });
    await expect(loadDecisionWorkableStages(
      { getWorkableStages },
      { workable_job_id: 'data-platform-lead' },
      { requireStagePick: true },
    )).resolves.toEqual(stages);
    expect(getWorkableStages).toHaveBeenCalledWith({
      shortcode: 'data-platform-lead',
    });
  });

  it('fails closed when a linked stage lookup is unavailable', async () => {
    await expect(loadDecisionWorkableStages(
      null,
      { workable_job_id: 'data-platform-lead' },
      { requireStagePick: true },
    )).rejects.toThrow('Workable stage lookup is unavailable.');
  });
});
