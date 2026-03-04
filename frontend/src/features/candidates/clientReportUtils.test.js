import { describe, expect, it } from 'vitest';

import { buildClientReportFilenameStem } from './clientReportUtils';

describe('clientReportUtils', () => {
  it('builds a report filename stem from role and candidate name', () => {
    expect(buildClientReportFilenameStem('AI Full Stack Engineer', 'Qaim Alvi')).toBe('AI Full Stack Engineer-Qaim Alvi');
  });

  it('sanitizes invalid filename characters', () => {
    expect(buildClientReportFilenameStem('Data / Platform Lead', 'Sam: Patel')).toBe('Data Platform Lead-Sam Patel');
  });
});
