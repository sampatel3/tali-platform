import { describe, expect, it, vi } from 'vitest';

import {
  APPLICATION_ROSTER_PAGE_SIZE,
  loadApplicationRoster,
} from './applicationRosterLoader';


describe('loadApplicationRoster', () => {
  it('continues after a duplicate-only offset page instead of truncating later rows', async () => {
    const first = Array.from({ length: APPLICATION_ROSTER_PAGE_SIZE }, (_, index) => ({
      id: index + 1,
      application_outcome: 'open',
    }));
    const tail = { id: 9999, application_outcome: 'open' };
    const listApplicationsPage = vi.fn((_roleId, params) => {
      if (params.application_outcome === 'rejected') {
        return Promise.resolve({ data: { items: [], total: 0 } });
      }
      if (params.offset === 0) {
        return Promise.resolve({ data: { items: first, total: 401 } });
      }
      if (params.offset === APPLICATION_ROSTER_PAGE_SIZE) {
        // Concurrent score/order changes can move an already-seen page across
        // an offset boundary. The loader must advance past it, not stop.
        return Promise.resolve({ data: { items: first, total: null } });
      }
      return Promise.resolve({ data: { items: [tail], total: null } });
    });

    const result = await loadApplicationRoster({
      rolesApi: { listApplicationsPage },
      roleId: 12,
    });

    expect(result.applications).toHaveLength(APPLICATION_ROSTER_PAGE_SIZE + 1);
    expect(result.applications.at(-1)).toEqual(tail);
    expect(listApplicationsPage.mock.calls.slice(0, 3).map(([, params]) => params.offset))
      .toEqual([0, 200, 400]);
  });

  it('stops a legacy server that ignores offset instead of looping forever', async () => {
    const fullPage = Array.from({ length: APPLICATION_ROSTER_PAGE_SIZE }, (_, index) => ({
      id: index + 1,
      application_outcome: 'open',
    }));
    const listApplications = vi.fn((_roleId, params) => Promise.resolve({
      data: params.application_outcome === 'open' ? fullPage : [],
    }));

    const result = await loadApplicationRoster({
      rolesApi: { listApplications },
      roleId: 12,
    });

    expect(result.applications).toHaveLength(APPLICATION_ROSTER_PAGE_SIZE);
    expect(listApplications).toHaveBeenCalledTimes(3);
  });
});
