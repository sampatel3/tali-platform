import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect } from 'vitest';

import { JobsNavMenu, NAV_TABS } from './Shell';
import { pathForPage } from '../../app/routing';

// Candidates live per-job (each role's pipeline) and the cross-job
// "what needs a decision" view is the Home hub, so the redundant top-level
// Candidates tab was removed. The candidate standing REPORT drill-down
// (/candidates/:applicationId) is unrelated and must stay.
describe('primary nav tabs', () => {
  it('no longer exposes a top-level Candidates tab', () => {
    const ids = NAV_TABS.map((tab) => tab.id);
    expect(ids).not.toContain('candidates');
    expect(NAV_TABS.some((tab) => tab.label === 'Candidates')).toBe(false);
  });

  it('keeps the surrounding recruiter tabs intact', () => {
    const ids = NAV_TABS.map((tab) => tab.id);
    expect(ids).toEqual(['home', 'jobs', 'chat', 'tasks', 'analytics', 'settings']);
  });
});

describe('candidate report route resolution', () => {
  it('still resolves the candidate standing report by application id', () => {
    expect(
      pathForPage('candidate-report', { candidateApplicationId: 'shr_abc123' }),
    ).toBe('/candidates/shr_abc123');
  });

  it('keeps navigation origin and viewed role as independent report context', () => {
    expect(pathForPage('candidate-report', {
      candidateApplicationId: 7,
      fromHome: true,
      viewRoleId: 135,
    })).toBe('/candidates/7?from=home&view_role_id=135');
    expect(pathForPage('candidate-report', {
      candidateApplicationId: 7,
      roleId: 135,
    })).toBe('/candidates/7?from=jobs/135');
  });

  it.each([null, undefined, '', 0, -1, 'not-a-role'])(
    'omits an invalid viewed role (%s)',
    (viewRoleId) => {
      expect(pathForPage('candidate-report', {
        candidateApplicationId: 7,
        fromHome: true,
        viewRoleId,
      })).toBe('/candidates/7?from=home');
    },
  );

  it('no longer resolves a top-level candidates list page', () => {
    expect(pathForPage('candidates')).toBeNull();
  });
});

describe('Jobs navigation menu', () => {
  it('uses recruiter-facing job language and links to the public job board', () => {
    render(
      <MemoryRouter>
        <JobsNavMenu active orgSlug="deep-light" />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: /Jobs/i }));

    expect(screen.getByRole('menuitem', { name: /All jobs/i })).toHaveAttribute('href', '/jobs');
    expect(screen.getByRole('menuitem', { name: /Create a job/i })).toHaveAttribute('href', '/requisitions');
    const jobBoardLink = screen.getByRole('menuitem', { name: /Job board/i });
    expect(jobBoardLink).toHaveAttribute('href', '/careers/deep-light');
    expect(jobBoardLink).toHaveAttribute('target', '_blank');
    expect(jobBoardLink).toHaveAttribute('rel', 'noreferrer');
    expect(screen.queryByText(/^Requisitions?$/i)).not.toBeInTheDocument();
  });
});
