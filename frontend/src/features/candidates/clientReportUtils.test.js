import { describe, expect, it } from 'vitest';

import {
  buildAssessmentReportIdentity,
  buildClientReportFilenameStem,
} from './clientReportUtils';

describe('clientReportUtils', () => {
  it('builds a report filename stem from role and candidate name', () => {
    expect(buildClientReportFilenameStem('AI Full Stack Engineer', 'Qaim Alvi')).toBe('AI Full Stack Engineer-Qaim Alvi');
  });

  it('sanitizes invalid filename characters', () => {
    expect(buildClientReportFilenameStem('Data / Platform Lead', 'Sam: Patel')).toBe('Data Platform Lead-Sam Patel');
  });

  it('builds assessment report identity from assessment payload', () => {
    expect(buildAssessmentReportIdentity({
      candidate_name: 'Sam Patel',
      candidate_email: 'sam@example.com',
      role_name: 'Platform Engineer',
      task_name: 'AWS Glue Recovery',
      application_status: 'applied',
      total_duration_seconds: 2700,
      completed_at: '2026-03-04T10:00:00Z',
    })).toMatchObject({
      name: 'Sam Patel',
      email: 'sam@example.com',
      roleName: 'Platform Engineer',
      taskName: 'AWS Glue Recovery',
      applicationStatus: 'applied',
      durationLabel: '45m',
    });
  });
});
