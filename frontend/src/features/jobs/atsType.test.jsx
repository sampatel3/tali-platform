import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { roleAtsType, atsTypeColumnLabel, AtsTypeTag } from './atsType';

describe('roleAtsType', () => {
  it('reads a Workable role from its source', () => {
    expect(roleAtsType({ source: 'workable' })).toBe('workable');
  });

  it('reads a Workable role from its workable_job_id even when source is unset', () => {
    expect(roleAtsType({ source: null, workable_job_id: 'wk_123' })).toBe('workable');
  });

  it('reads a Bullhorn role from its source', () => {
    expect(roleAtsType({ source: 'bullhorn' })).toBe('bullhorn');
  });

  it('reads a Bullhorn role from its bullhorn_job_order_id', () => {
    expect(roleAtsType({ source: 'manual', bullhorn_job_order_id: '4471' })).toBe('bullhorn');
  });

  it('treats a native Taali role as full_ats', () => {
    expect(roleAtsType({ source: 'manual' })).toBe('full_ats');
  });

  it('defaults a role with no source at all to full_ats', () => {
    expect(roleAtsType({})).toBe('full_ats');
    expect(roleAtsType(null)).toBe('full_ats');
  });

  it('is case-insensitive on source', () => {
    expect(roleAtsType({ source: 'WORKABLE' })).toBe('workable');
  });
});

describe('atsTypeColumnLabel', () => {
  it('labels the candidate stage column by who owns the pipeline', () => {
    expect(atsTypeColumnLabel({ source: 'workable' })).toBe('Workable');
    expect(atsTypeColumnLabel({ source: 'bullhorn' })).toBe('Bullhorn');
    expect(atsTypeColumnLabel({ source: 'manual' })).toBe('Pipeline');
  });
});

describe('AtsTypeTag', () => {
  it('renders the Full ATS badge for a native role', () => {
    render(<AtsTypeTag role={{ source: 'manual' }} />);
    expect(screen.getByText('Full ATS')).toBeInTheDocument();
  });

  it('renders the Workable badge for a synced role', () => {
    render(<AtsTypeTag role={{ source: 'workable' }} />);
    expect(screen.getByText('Workable')).toBeInTheDocument();
    expect(screen.queryByText('Full ATS')).not.toBeInTheDocument();
  });

  it('renders the Bullhorn badge for a bullhorn role', () => {
    render(<AtsTypeTag role={{ bullhorn_job_order_id: '99' }} />);
    expect(screen.getByText('Bullhorn')).toBeInTheDocument();
  });
});
