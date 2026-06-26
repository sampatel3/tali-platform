import { describe, it, expect } from 'vitest';

import { deriveAssessmentWorkflow, summarizeAssessmentWorkflow } from './AssessmentWorkflow';

const codes = (status, tracking) => deriveAssessmentWorkflow(status, tracking).codes;

describe('deriveAssessmentWorkflow', () => {
  it('sent — delivery pending', () => {
    const wf = deriveAssessmentWorkflow('pending', { invite_sent_at: 'x', email_status: 'sent' });
    expect(wf.codes).toBe('DCTTT');
    expect(wf.label).toMatch(/delivery pending/i);
  });

  it('delivered to inbox', () => {
    expect(codes('pending', { delivered_at: 'x', email_status: 'delivered' })).toBe('DDCTT');
  });

  it('opened but not started → nudge', () => {
    const wf = deriveAssessmentWorkflow('pending', { opened_at: 'x', email_status: 'opened' });
    expect(wf.codes).toBe('DDDCT');
    expect(wf.action).toBe('nudge');
    expect(wf.live).toBe(false);
  });

  it('in progress → live', () => {
    const wf = deriveAssessmentWorkflow('in_progress', { started_at: 'x' });
    expect(wf.codes).toBe('DDDCT');
    expect(wf.live).toBe(true);
  });

  it('completed (both completed statuses)', () => {
    expect(codes('completed', {})).toBe('DDDDD');
    expect(codes('completed_due_to_timeout', {})).toBe('DDDDD');
  });

  it('expired after opening → amber, resend', () => {
    const wf = deriveAssessmentWorkflow('expired', { opened_at: 'x' });
    expect(wf.codes).toBe('DDDWT');
    expect(wf.tone).toBe('warn');
    expect(wf.action).toBe('resend');
  });

  it('not sent (provider failure) → red at Sent, resend', () => {
    const wf = deriveAssessmentWorkflow('pending', { invite_sent_at: 'x', email_status: 'failed' });
    expect(wf.codes).toBe('ETTTT');
    expect(wf.tone).toBe('err');
    expect(wf.action).toBe('resend');
  });

  it('bounced → red at Delivered', () => {
    expect(codes('pending', { email_status: 'bounced' })).toBe('DETTT');
  });
});

describe('summarizeAssessmentWorkflow', () => {
  it('produces cumulative funnel counts and isolates not-sent', () => {
    const cands = [
      { score_summary: { assessment_status: 'pending', invite_tracking: { invite_sent_at: 'x', email_status: 'sent' } } },
      { score_summary: { assessment_status: 'pending', invite_tracking: { delivered_at: 'x', email_status: 'delivered' } } },
      { score_summary: { assessment_status: 'pending', invite_tracking: { opened_at: 'x', email_status: 'opened' } } },
      { score_summary: { assessment_status: 'in_progress', invite_tracking: { started_at: 'x' } } },
      { score_summary: { assessment_status: 'pending', invite_tracking: { email_status: 'failed' } } },
    ];
    const f = summarizeAssessmentWorkflow(cands);
    expect(f).toEqual({ total: 5, delivered: 3, opened: 2, inProgress: 1, notSent: 1 });
  });
});
