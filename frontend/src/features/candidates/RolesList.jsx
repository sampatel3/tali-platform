import React from 'react';
import { ChevronsUpDown, Loader2, Plus } from 'lucide-react';

import {
  Badge,
  Button,
  Card,
  EmptyState,
  Panel,
  cx,
} from '../../shared/ui/TaaliPrimitives';

export const RolesList = ({ roles, selectedRoleId, loading, error, onSelectRole, onCreateRole }) => (
  <Panel className="p-4">
    <div className="mb-4 flex items-center justify-between">
      <h2 className="text-sm font-semibold uppercase tracking-[0.08em] text-gray-600">Roles</h2>
      <Button type="button" variant="ghost" size="sm" onClick={onCreateRole}>
        <Plus size={14} />
        New
      </Button>
    </div>

    {loading ? (
      <Card className="px-3 py-4">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <Loader2 size={14} className="animate-spin" />
          Loading roles...
        </div>
      </Card>
    ) : null}

    {!loading && error ? (
      <Card className="border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700">
        {error}
      </Card>
    ) : null}

    {!loading && !error && roles.length === 0 ? (
      <EmptyState
        title="No roles yet"
        description="Create your first role to start adding candidates."
        action={(
          <Button type="button" variant="primary" size="sm" onClick={onCreateRole}>
            <Plus size={14} />
            Create your first role
          </Button>
        )}
      />
    ) : null}

    {!loading && !error && roles.length > 0 ? (
      <ul className="space-y-2">
        {roles.map((role) => {
          const selected = String(role.id) === String(selectedRoleId);
          const specReady = Boolean(role.job_spec_present || role.job_spec_filename);
          const specLabel = specReady
            ? (role.job_spec_filename
              ? 'Spec uploaded'
              : (role.source === 'workable' ? 'Spec imported' : 'Spec ready'))
            : 'No spec';
          return (
            <li key={role.id}>
              <button
                type="button"
                onClick={() => onSelectRole(String(role.id))}
                className={cx(
                  'w-full text-left border-2 px-3 py-3 transition',
                  selected
                    ? 'border-[var(--taali-purple)] bg-[var(--taali-purple-soft)]'
                    : 'border-[var(--taali-border-muted)] bg-[var(--taali-surface)] hover:border-[var(--taali-border)]'
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-[var(--taali-text)]">{role.name}</p>
                    <p className="mt-0.5 text-xs text-[var(--taali-muted)]">
                      {role.applications_count || 0} candidate{(role.applications_count || 0) === 1 ? '' : 's'}
                    </p>
                  </div>
                  <ChevronsUpDown size={14} className="mt-0.5 shrink-0 text-gray-400" />
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge variant={specReady ? 'success' : 'warning'}>
                    {specLabel}
                  </Badge>
                  <Badge variant="purple">Tasks: {role.tasks_count || 0}</Badge>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    ) : null}
  </Panel>
);
