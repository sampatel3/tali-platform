import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  agentIntakeLifecycleCopy,
  applicationAtsStage,
  atsProviderLabel,
  atsTypeColumnLabel,
  AtsTypeTag,
  organizationAtsProvider,
  roleAtsProvider,
  roleAtsType,
  roleExternalJobId,
  roleExternalJobLive,
  roleExternalJobState,
} from './atsType';

describe('roleAtsType', () => {
  it.each([
    ['workable', 'wk_123', 'published', true, 'Workable'],
    ['bullhorn', 'bh_4471', 'open', true, 'Bullhorn'],
  ])(
    'uses the provider-neutral %s lifecycle contract',
    (provider, externalJobId, state, live, label) => {
      const role = {
        ats_provider: provider,
        external_job_id: externalJobId,
        external_job_state: state,
        external_job_live: live,
      };
      expect(roleAtsType(role)).toBe(provider);
      expect(roleAtsProvider(role)).toBe(provider);
      expect(atsProviderLabel(provider)).toBe(label);
      expect(roleExternalJobId(role)).toBe(externalJobId);
      expect(roleExternalJobState(role)).toBe(state);
      expect(roleExternalJobLive(role)).toBe(true);
    },
  );

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

describe('provider-neutral ATS helpers', () => {
  it.each([
    [{ active_ats: 'workable', workable_connected: true }, 'workable'],
    [{ active_ats: 'bullhorn', bullhorn_connected: true }, 'bullhorn'],
  ])('honors the organization active_ats selection', (organization, expected) => {
    expect(organizationAtsProvider(organization)).toBe(expected);
  });

  it('does not revive a stale connection when the server explicitly says standalone', () => {
    expect(organizationAtsProvider({
      active_ats: 'standalone',
      workable_connected: true,
      bullhorn_connected: true,
    })).toBeNull();
  });

  it('keeps legacy connection-only payloads backward compatible', () => {
    expect(organizationAtsProvider({ workable_connected: true })).toBe('workable');
    expect(organizationAtsProvider({ bullhorn_connected: true })).toBe('bullhorn');
  });

  it.each([
    ['workable', { workable_stage: 'Phone screen' }, 'Phone screen'],
    ['bullhorn', { external_stage_raw: 'Interview Scheduled' }, 'Interview Scheduled'],
  ])('renders the raw %s pipeline stage', (provider, application, expected) => {
    expect(applicationAtsStage(application, provider)).toBe(expected);
  });

  it('does not reuse the legacy Workable live flag for Bullhorn', () => {
    expect(roleExternalJobLive({
      ats_provider: 'bullhorn',
      workable_job_live: true,
    })).toBeNull();
  });

  it('preserves the operational provider on a Bullhorn sister role', () => {
    const sisterRole = {
      role_kind: 'sister',
      ats_owner_role_id: 41,
      ats_provider: 'bullhorn',
      external_job_id: 'BH-41',
    };
    expect(roleAtsType(sisterRole)).toBe('sister');
    expect(roleAtsProvider(sisterRole)).toBe('bullhorn');
    expect(atsTypeColumnLabel(sisterRole)).toBe('Bullhorn');
    render(<AtsTypeTag role={sisterRole} />);
    expect(screen.getByText('Related · Bullhorn')).toBeInTheDocument();
  });

  it('distinguishes the native intake hold from an external ATS posting', () => {
    const copy = agentIntakeLifecycleCopy({ ats_provider: 'bullhorn' });
    expect(copy).toContain('Taali native job page');
    expect(copy).toContain('Bullhorn intake is not closed by Taali');
    expect(copy).not.toContain('same intake hold');
    expect(agentIntakeLifecycleCopy({ source: 'manual' })).toContain(
      'applications close until Resume or Turn on',
    );
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
