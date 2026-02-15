import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, UserPlus } from 'lucide-react';

import {
  Badge,
  Button,
  EmptyState,
  Panel,
  Select,
  TableShell,
} from '../../shared/ui/TaaliPrimitives';
import { formatDateTime, statusVariant } from './candidatesUiUtils';

export const CandidatesTable = ({
  applications,
  loading,
  error,
  searchQuery,
  roleTasks,
  canCreateAssessment,
  creatingAssessmentId,
  viewingApplicationId,
  onAddCandidate,
  onViewCandidate,
  onCreateAssessment,
}) => {
  const [composerApplicationId, setComposerApplicationId] = useState(null);
  const [taskByApplication, setTaskByApplication] = useState({});

  useEffect(() => {
    setComposerApplicationId(null);
  }, [applications, roleTasks]);

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return applications;
    const query = searchQuery.toLowerCase();
    return applications.filter((app) => (
      [
        app.candidate_name,
        app.candidate_email,
        app.candidate_position,
        app.status,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query)
    ));
  }, [applications, searchQuery]);

  if (loading) {
    return (
      <Panel className="px-4 py-10 text-center text-sm text-gray-500">
        <div className="inline-flex items-center gap-2">
          <Loader2 size={15} className="animate-spin" />
          Loading candidates...
        </div>
      </Panel>
    );
  }

  if (error) {
    return (
      <Panel className="px-4 py-10 text-center text-sm border-red-200 bg-red-50 text-red-700">
        {error}
      </Panel>
    );
  }

  if (filtered.length === 0) {
    return (
      <EmptyState
        title="No candidates yet"
        description="Add your first candidate to this role and start assessments."
        action={(
          <Button type="button" variant="primary" size="sm" onClick={onAddCandidate}>
            <UserPlus size={15} />
            Add candidate
          </Button>
        )}
      />
    );
  }

  return (
    <TableShell>
      <table className="min-w-[760px]">
        <thead>
          <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
            <th className="px-4 py-3">Name</th>
            <th className="px-4 py-3">Email</th>
            <th className="px-4 py-3">Position</th>
            <th className="px-4 py-3">Rank</th>
            <th className="px-4 py-3">Workable</th>
            <th className="px-4 py-3">CV match</th>
            <th className="px-4 py-3">Source</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Last activity</th>
            <th className="px-4 py-3">Actions</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((app) => {
            const selectedTask = taskByApplication[app.id] || (roleTasks.length === 1 ? String(roleTasks[0].id) : '');
            const canOpenComposer = Boolean(canCreateAssessment && app.cv_filename && roleTasks.length > 0);

            return (
              <React.Fragment key={app.id}>
                <tr className="align-top">
                  <td className="px-4 py-3 text-sm font-semibold text-gray-900">
                    {app.candidate_name || app.candidate_email}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_email}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_position || '—'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    {typeof app.rank_score === 'number'
                      ? `${app.rank_score.toFixed(1)}/10`
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    {typeof app.workable_score === 'number'
                      ? `${app.workable_score.toFixed(1)}/10`
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">
                    {typeof app.cv_match_score === 'number'
                      ? `${app.cv_match_score.toFixed(1)}/10`
                      : (
                        app.cv_filename
                          ? (app.cv_match_details?.error ? 'Unavailable' : 'Pending')
                          : '—'
                      )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.source || 'manual'}</td>
                  <td className="px-4 py-3">
                    <Badge variant={statusVariant(app.status)}>{app.status || 'applied'}</Badge>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700">{formatDateTime(app.updated_at || app.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => onViewCandidate(app)}
                        disabled={viewingApplicationId === app.id}
                      >
                        {viewingApplicationId === app.id ? 'Loading...' : 'View'}
                      </Button>
                      {canCreateAssessment ? (
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          onClick={() => {
                            if (composerApplicationId === app.id) {
                              setComposerApplicationId(null);
                              return;
                            }
                            if (roleTasks.length === 1) {
                              setTaskByApplication((prev) => ({ ...prev, [app.id]: String(roleTasks[0].id) }));
                            }
                            setComposerApplicationId(app.id);
                          }}
                          disabled={!canOpenComposer}
                        >
                          Create assessment
                        </Button>
                      ) : null}
                    </div>
                    {!app.cv_filename ? (
                      <p className="mt-1 text-xs text-amber-700">CV is required before assessment.</p>
                    ) : null}
                    {app.cv_filename && roleTasks.length === 0 ? (
                      <p className="mt-1 text-xs text-amber-700">Link at least one task to this role first.</p>
                    ) : null}
                  </td>
                </tr>

                {composerApplicationId === app.id ? (
                  <tr className="bg-[#faf8ff]">
                    <td colSpan={10} className="px-4 py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          value={selectedTask}
                          onChange={(event) => {
                            setTaskByApplication((prev) => ({ ...prev, [app.id]: event.target.value }));
                          }}
                          className="min-w-[240px]"
                        >
                          <option value="">Select task...</option>
                          {roleTasks.map((task) => (
                            <option key={task.id} value={task.id}>{task.name}</option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          onClick={async () => {
                            const success = await onCreateAssessment(app, selectedTask);
                            if (success) setComposerApplicationId(null);
                          }}
                          disabled={!selectedTask || creatingAssessmentId === app.id}
                        >
                          {creatingAssessmentId === app.id ? 'Creating...' : 'Send assessment'}
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setComposerApplicationId(null)}
                        >
                          Cancel
                        </Button>
                      </div>
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </TableShell>
  );
};
