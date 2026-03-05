import React, { useEffect, useMemo, useState } from 'react';
import { BriefcaseBusiness, ChevronRight, Search } from 'lucide-react';

import * as apiClient from '../../shared/api';
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  PageContainer,
  PageHeader,
  Panel,
  Spinner,
} from '../../shared/ui/TaaliPrimitives';

const STAGES = [
  { key: 'applied', label: 'Applied' },
  { key: 'invited', label: 'Invited' },
  { key: 'in_assessment', label: 'In assessment' },
  { key: 'review', label: 'Review' },
];

export const JobsPage = ({ onNavigate, NavComponent = null }) => {
  const rolesApi = apiClient.roles;
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await rolesApi.list({ include_pipeline_stats: true });
        if (cancelled) return;
        setRoles(Array.isArray(res?.data) ? res.data : []);
      } catch {
        if (cancelled) return;
        setRoles([]);
        setError('Failed to load jobs.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [rolesApi]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return roles;
    return roles.filter((role) => String(role?.name || '').toLowerCase().includes(needle));
  }, [query, roles]);

  return (
    <div>
      {NavComponent ? <NavComponent currentPage="jobs" onNavigate={onNavigate} /> : null}
      <PageContainer density="compact" width="wide">
        <PageHeader
          title="Jobs"
          subtitle="Manage recruiter workflow from role-level pipeline views."
          actions={(
            <Button variant="secondary" onClick={() => onNavigate('candidates')}>
              Open candidates
            </Button>
          )}
        />

        <Panel className="mb-4 p-3">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--taali-muted)]" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="pl-9"
              placeholder="Search jobs"
            />
          </div>
        </Panel>

        {loading ? (
          <div className="flex min-h-[240px] items-center justify-center">
            <Spinner size={20} />
          </div>
        ) : error ? (
          <Panel className="border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4 text-sm text-[var(--taali-danger)]">
            {error}
          </Panel>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No jobs found"
            description="Create a role from Candidates to start building your pipeline."
            action={<Button onClick={() => onNavigate('candidates')}>Go to candidates</Button>}
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {filtered.map((role) => {
              const stageCounts = role?.stage_counts || {};
              return (
                <Card key={role.id} className="space-y-3 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-base font-semibold text-[var(--taali-text)]">{role.name}</p>
                      <p className="text-xs text-[var(--taali-muted)]">
                        {role.active_candidates_count || role.applications_count || 0} active candidates
                      </p>
                    </div>
                    <Badge variant="purple">
                      <BriefcaseBusiness size={12} />
                      Job
                    </Badge>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-xs">
                    {STAGES.map((stage) => (
                      <div
                        key={stage.key}
                        className="rounded-[var(--taali-radius-control)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-2 py-1.5"
                      >
                        <div className="text-[var(--taali-muted)]">{stage.label}</div>
                        <div className="font-semibold text-[var(--taali-text)]">{Number(stageCounts?.[stage.key] || 0)}</div>
                      </div>
                    ))}
                  </div>

                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => onNavigate('job-pipeline', { roleId: role.id })}
                    >
                      Open pipeline <ChevronRight size={14} />
                    </Button>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </PageContainer>
    </div>
  );
};

export default JobsPage;
