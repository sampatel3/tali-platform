import { describe, expect, it } from 'vitest';

import {
  buildRequisitionAtsSpec,
  isRelatedRoleBrief,
  isPublishedRequisition,
  isRequisitionBriefReadOnly,
  isSupportedRequisitionAttachment,
  REQUISITION_ATTACHMENT_ACCEPT,
  REQUISITION_ATTACHMENT_MAX_BYTES,
  reloadRequisitionAfterRoleConflict,
  requisitionAtsBridgeModel,
  requisitionAtsProvider,
  requisitionDisplayTitle,
  requisitionGapLabels,
  requisitionHeaderStatusLabel,
  requisitionPublishBlockedMessage,
  requisitionRoleReference,
  requisitionRoleConflictMessage,
  requisitionSourceRoleReference,
  requisitionStatusLabel,
  validateRequisitionAttachments,
} from './RequisitionsPage';

const attachment = (name, type, size = 100) => ({ name, type, size });

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

  it('keeps a published linked brief editable while preserving legacy archives', () => {
    expect(isRequisitionBriefReadOnly({
      status: 'draft',
      job: { role_id: 42, version: 7 },
      job_page: { token: 'published-page' },
    })).toBe(false);
    expect(isRequisitionBriefReadOnly({ status: 'applied' })).toBe(true);
  });

  it('identifies persisted related-role drafts independently of lifecycle status', () => {
    expect(isRelatedRoleBrief({ brief_kind: 'related_role', status: 'draft' })).toBe(true);
    expect(isRelatedRoleBrief({ source_role_id: 42, status: 'applied' })).toBe(true);
    expect(isRelatedRoleBrief({ brief_kind: 'standard', status: 'draft' })).toBe(false);
  });

  it('keeps related-role titles and the compact header status present', () => {
    const relatedDraft = {
      brief_kind: 'related_role',
      source_role_id: 42,
      source_role: { role_id: 42, name: 'AI Engineer' },
      title: '   ',
      status: 'draft',
    };

    expect(requisitionDisplayTitle(relatedDraft)).toBe('AI Engineer #42 · Related');
    expect(requisitionHeaderStatusLabel(relatedDraft)).toBe('Related draft');
    expect(requisitionHeaderStatusLabel({ ...relatedDraft, status: 'applied' })).toBe('Related role');
    expect(requisitionDisplayTitle({ title: 'Platform AI Engineer' })).toBe('Platform AI Engineer');
    expect(requisitionDisplayTitle({ title: '   ' })).toBe('Untitled job');
  });

  it('renders complete role references with graceful partial fallbacks', () => {
    expect(requisitionRoleReference('AI Engineer', 42)).toBe('AI Engineer #42');
    expect(requisitionRoleReference('AI Engineer #42', 42)).toBe('AI Engineer #42');
    expect(requisitionRoleReference('AI Engineer', null)).toBe('Role');
    expect(requisitionRoleReference('', 42)).toBe('Role');
    expect(requisitionSourceRoleReference({
      source_role_id: 42,
      source_role: { role_id: 42, name: 'AI Engineer' },
    })).toBe('AI Engineer #42');
  });

  it('reloads the authoritative requisition after a stale linked write', async () => {
    const error = {
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_VERSION_CONFLICT',
            message: 'This job changed after you opened it.',
            current_version: 8,
            current_role: { id: 42, version: 8, job_status: 'open' },
            changed_by: { name: 'Aisha Khan' },
          },
        },
      },
    };
    const latest = {
      id: 3,
      status: 'draft',
      custom_fields: { relocation_support: 'yes' },
      job: { role_id: 42, version: 8, job_status: 'open' },
    };
    const result = await reloadRequisitionAfterRoleConflict(
      3,
      error,
      async () => latest,
    );

    expect(result.brief).toBe(latest);
    expect(result.brief.custom_fields).toEqual({ relocation_support: 'yes' });
    expect(requisitionRoleConflictMessage(error)).toContain('Aisha Khan');
    expect(requisitionRoleConflictMessage(error)).toContain('review and try again');
  });

  it('does not advance the local version when the conflict refresh fails', async () => {
    const error = {
      response: {
        status: 409,
        data: {
          detail: {
            code: 'ROLE_VERSION_CONFLICT',
            current_version: 8,
            current_role: { id: 42, version: 8 },
          },
        },
      },
    };

    const result = await reloadRequisitionAfterRoleConflict(
      3,
      error,
      async () => null,
    );

    expect(result.brief).toBeNull();
    expect(result.message).toContain('could not be loaded');
    expect(result.message).toContain('before retrying');
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

describe('requisition attachment guardrails', () => {
  it('offers DOCX and exact supported image formats without broad image/*', () => {
    expect(REQUISITION_ATTACHMENT_ACCEPT).toContain('.docx');
    expect(REQUISITION_ATTACHMENT_ACCEPT).toContain('.webp');
    expect(REQUISITION_ATTACHMENT_ACCEPT).not.toContain('image/*');

    expect(isSupportedRequisitionAttachment(attachment(
      'job-spec.docx',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    ))).toBe(true);
    expect(isSupportedRequisitionAttachment(attachment('role.webp', 'image/webp'))).toBe(true);
    expect(isSupportedRequisitionAttachment(attachment('role.svg', 'image/svg+xml'))).toBe(false);
    expect(isSupportedRequisitionAttachment(attachment('role.heic', 'image/heic'))).toBe(false);
    expect(isSupportedRequisitionAttachment(attachment('renamed.pdf', 'image/heic'))).toBe(false);
    expect(isSupportedRequisitionAttachment(attachment('renamed.jpg', 'application/pdf'))).toBe(false);
    expect(isSupportedRequisitionAttachment(attachment('unknown.pdf', 'application/octet-stream'))).toBe(true);
    expect(isSupportedRequisitionAttachment(attachment('transcript.srt', 'application/x-subrip'))).toBe(true);
  });

  it('matches the backend six-file and 15 MB per-file limits', () => {
    const existing = Array.from({ length: 5 }, (_, index) => attachment(`note-${index}.txt`, 'text/plain'));
    const tooMany = validateRequisitionAttachments(existing, [
      attachment('six.txt', 'text/plain'),
      attachment('seven.txt', 'text/plain'),
    ]);
    expect(tooMany.files).toEqual([]);
    expect(tooMany.error).toContain('up to 6 files');

    const oversized = validateRequisitionAttachments([], [
      attachment('large.pdf', 'application/pdf', REQUISITION_ATTACHMENT_MAX_BYTES + 1),
    ]);
    expect(oversized.files).toEqual([]);
    expect(oversized.error).toContain('large.pdf');
    expect(oversized.error).toContain('15 MB');
  });

  it('rejects unsupported selections and preserves a valid selection as-is', () => {
    const svg = attachment('diagram.svg', 'image/svg+xml');
    expect(validateRequisitionAttachments([], [svg]).error).toContain('isn\'t supported');

    const files = [
      attachment('spec.pdf', 'application/pdf'),
      attachment('notes.md', 'text/markdown'),
    ];
    expect(validateRequisitionAttachments([], files)).toEqual({ files, error: '' });
  });
});

describe('requisition publish blockers', () => {
  const gaps = [
    { key: 'responsibilities', label: 'Key responsibilities' },
    { key: 'success_profile', label: 'Success profile' },
    { key: 'success_profile', label: 'Success profile' },
  ];

  it('uses exact, de-duplicated field labels with a key fallback', () => {
    expect(requisitionGapLabels(gaps)).toEqual(['Key responsibilities', 'Success profile']);
    expect(requisitionGapLabels([{ key: 'target_start_date' }])).toEqual(['Target Start Date']);
  });

  it('uses the correct action language for normal and related roles', () => {
    expect(requisitionPublishBlockedMessage(gaps)).toBe(
      'Complete the required Brief fields before you can publish this job: Key responsibilities, Success profile.',
    );
    expect(requisitionPublishBlockedMessage(gaps, { relatedRole: true })).toBe(
      'Complete the required Brief fields before you can create and score candidates: Key responsibilities, Success profile.',
    );
  });
});
