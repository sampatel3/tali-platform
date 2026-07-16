import { describe, expect, it } from 'vitest';

import { mergeRoleShell } from './roleShellMerge';

describe('mergeRoleShell', () => {
  it('updates runtime truth without replacing detail and aggregate omissions', () => {
    const current = {
      id: 101,
      version: 7,
      description: 'Detailed source description',
      criteria: [{ id: 4, text: 'Preserve me' }],
      job_spec_text: 'Full job specification',
      interview_focus: { role_summary: 'Focus' },
      screening_pack_template: { stage: 'screening' },
      tech_interview_pack_template: { stage: 'tech_stage_2' },
      requisition: { id: 31 },
      client_id: 8,
      client_name: 'Client',
      sister_role_count: 2,
      tasks_count: 3,
      applications_count: 9,
      stage_counts: { applied: 5 },
      pending_decisions_by_type: { rejection: 2 },
      active_candidates_count: 7,
      is_published: true,
      job_spec_filename: 'cached-spec.pdf',
      job_spec_uploaded_at: '2026-07-15T10:00:00Z',
      job_spec_manually_edited_at: '2026-07-15T11:00:00Z',
      job_spec_present: true,
      interview_focus_generated_at: '2026-07-15T12:00:00Z',
      agentic_mode_enabled: false,
      assessment_task_provisioning: { activation_intent: { status: 'pending' } },
    };
    const shell = {
      id: 101,
      version: 8,
      description: null,
      criteria: [],
      job_spec_text: null,
      interview_focus: null,
      screening_pack_template: null,
      tech_interview_pack_template: null,
      requisition: null,
      client_id: null,
      client_name: null,
      sister_role_count: 0,
      tasks_count: 0,
      applications_count: 0,
      stage_counts: {},
      pending_decisions_by_type: {},
      active_candidates_count: 0,
      is_published: false,
      job_spec_filename: 'newer-spec.pdf',
      job_spec_uploaded_at: '2026-07-16T10:00:00Z',
      job_spec_manually_edited_at: '2026-07-16T11:00:00Z',
      job_spec_present: false,
      interview_focus_generated_at: '2026-07-16T12:00:00Z',
      agentic_mode_enabled: true,
      assessment_task_provisioning: { activation_intent: { status: 'succeeded' } },
    };

    expect(mergeRoleShell(current, shell)).toEqual({
      ...current,
      version: 8,
      agentic_mode_enabled: true,
      assessment_task_provisioning: shell.assessment_task_provisioning,
    });
  });

  it('uses the shell directly for a cold or different-role paint', () => {
    const shell = { id: 202, version: 3, name: 'Different role' };

    expect(mergeRoleShell(null, shell)).toBe(shell);
    expect(mergeRoleShell({ id: 101, description: 'Old role' }, shell)).toBe(shell);
  });
});
