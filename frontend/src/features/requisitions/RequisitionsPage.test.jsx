import { describe, expect, it } from 'vitest';

import {
  buildRequisitionAtsSpec,
  isPublishedRequisition,
  requisitionAtsBridgeModel,
  requisitionAtsProvider,
  requisitionStatusLabel,
} from './RequisitionsPage';

describe('requisition lifecycle labels', () => {
  it('maps backend submitted/applied states to recruiter-facing lifecycle language', () => {
    expect(requisitionStatusLabel('draft')).toBe('Draft');
    expect(requisitionStatusLabel('submitted')).toBe('Ready to publish');
    expect(requisitionStatusLabel('applied')).toBe('Published');
    expect(isPublishedRequisition('submitted')).toBe(false);
    expect(isPublishedRequisition('applied')).toBe(true);
  });

  it('keeps legacy published payloads compatible', () => {
    expect(requisitionStatusLabel('published')).toBe('Published');
    expect(isPublishedRequisition('published')).toBe(true);
  });
});

describe('requisition ATS bridge', () => {
  it.each([
    ['workable', { active_ats: 'workable', workable_connected: true }],
    ['bullhorn', { active_ats: 'bullhorn', bullhorn_connected: true }],
  ])('uses %s as the organization-owned intake provider', (provider, organization) => {
    expect(requisitionAtsProvider(organization, null)).toBe(provider);
  });

  it('lets the linked job provider remain authoritative during rollout', () => {
    expect(requisitionAtsProvider(
      { active_ats: 'workable', workable_connected: true },
      { ats_provider: 'bullhorn', external_job_id: 'BH-42' },
    )).toBe('bullhorn');
  });

  it('builds one provider-neutral stamped specification', () => {
    expect(buildRequisitionAtsSpec('## Role\nBuild useful systems.', 'RQ-ABC123')).toContain(
      'Taali ref: RQ-ABC123',
    );
  });

  it.each([
    ['workable', 'Workable'],
    ['bullhorn', 'Bullhorn'],
  ])('never prompts a duplicate %s job after linkage', (provider, label) => {
    const linked = requisitionAtsBridgeModel(provider, `${provider}-job-41`);
    expect(linked.linked).toBe(true);
    expect(linked.copyLabel).toBeNull();
    expect(linked.hint).toContain(`already linked to ${label}`);
    expect(linked.hint).toContain('do not create another one');

    const unlinked = requisitionAtsBridgeModel(provider, null);
    expect(unlinked.linked).toBe(false);
    expect(unlinked.copyLabel).toBe(`Optional: copy for ${label}`);
  });
});
