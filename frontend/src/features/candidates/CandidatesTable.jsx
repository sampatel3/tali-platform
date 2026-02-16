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
  searchQuery = '',
  statusFilter = 'all',
  sortBy = 'rank_score',
  sortOrder = 'desc',
  roleTasks,
  canCreateAssessment,
  creatingAssessmentId,
  viewingApplicationId,
  onChangeSort,
  onAddCandidate,
  onViewCandidate,
  onCreateAssessment,
}) => {
  const [composerApplicationId, setComposerApplicationId] = useState(null);
  const [taskByApplication, setTaskByApplication] = useState({});

  const sortableColumns = {
    rank_score: 'Rank',
    workable_score: 'Workable',
    cv_match_score: 'CV match',
    created_at: 'Last activity',
  };

  useEffect(() => {
    setComposerApplicationId(null);
  }, [applications, roleTasks]);

  const filtered = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return applications.filter((app) => {
      if (statusFilter !== 'all' && (app.status || 'applied').toLowerCase() !== statusFilter) return false;
      if (!query) return true;
      return (
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
      );
    });
  }, [applications, searchQuery, statusFilter]);

  const handleSortToggle = (column) => {
    if (!onChangeSort || !sortableColumns[column]) return;
    if (column === sortBy) {
      onChangeSort(column, sortOrder === 'asc' ? 'desc' : 'asc');
      return;
    }
    onChangeSort(column, 'desc');
  };

  const renderSortIndicator = (column) => {
    if (sortBy !== column) return '-';
    return sortOrder === 'asc' ? '^' : 'v';
  };

  const hasClientFilters = Boolean(searchQuery.trim()) || statusFilter !== 'all';

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
        title={hasClientFilters ? 'No matching candidates' : 'No candidates yet'}
        description={hasClientFilters
          ? 'Adjust search or status filters to see more candidates.'
          : 'Add your first candidate to this role and start assessments.'}
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
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-xs text-gray-500">
          {filtered.length}
          {' '}
          candidate{filtered.length === 1 ? '' : 's'}
        </p>
        <p className="text-xs text-gray-500">Scroll horizontally to see every column.</p>
      </div>
      <TableShell className="max-h-[68vh]">
        <table className="min-w-[1460px]">
          <thead>
          <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
            <th className="sticky left-0 top-0 z-30 min-w-[220px] bg-[#f4f1fb] px-4 py-3">Name</th>
            <th className="sticky top-0 z-20 min-w-[250px] bg-[#f4f1fb] px-4 py-3">Email</th>
            <th className="sticky top-0 z-20 min-w-[210px] bg-[#f4f1fb] px-4 py-3">Position</th>
            <th
              className="sticky top-0 z-20 min-w-[120px] bg-[#f4f1fb] px-4 py-3"
              aria-sort={sortBy === 'rank_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
            >
              <button
                type="button"
                className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                onClick={() => handleSortToggle('rank_score')}
              >
                Rank
                <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('rank_score')}</span>
              </button>
            </th>
            <th
              className="sticky top-0 z-20 min-w-[130px] bg-[#f4f1fb] px-4 py-3"
              aria-sort={sortBy === 'workable_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
            >
              <button
                type="button"
                className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                onClick={() => handleSortToggle('workable_score')}
              >
                Workable
                <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('workable_score')}</span>
              </button>
            </th>
            <th
              className="sticky top-0 z-20 min-w-[130px] bg-[#f4f1fb] px-4 py-3"
              aria-sort={sortBy === 'cv_match_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
            >
              <button
                type="button"
                className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                onClick={() => handleSortToggle('cv_match_score')}
              >
                CV match
                <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('cv_match_score')}</span>
              </button>
            </th>
            <th className="sticky top-0 z-20 min-w-[130px] bg-[#f4f1fb] px-4 py-3">Source</th>
            <th className="sticky top-0 z-20 min-w-[140px] bg-[#f4f1fb] px-4 py-3">Status</th>
            <th
              className="sticky top-0 z-20 min-w-[170px] bg-[#f4f1fb] px-4 py-3"
              aria-sort={sortBy === 'created_at' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
            >
              <button
                type="button"
                className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                onClick={() => handleSortToggle('created_at')}
              >
                Last activity
                <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('created_at')}</span>
              </button>
            </th>
            <th className="sticky top-0 z-20 min-w-[250px] bg-[#f4f1fb] px-4 py-3">Actions</th>
          </tr>
          </thead>
          <tbody>
          {filtered.map((app) => {
            const selectedTask = taskByApplication[app.id] || (roleTasks.length === 1 ? String(roleTasks[0].id) : '');
            const canOpenComposer = Boolean(canCreateAssessment && app.cv_filename && roleTasks.length > 0);

            return (
              <React.Fragment key={app.id}>
                <tr className="group align-top">
                  <td className="sticky left-0 z-10 bg-[var(--taali-surface)] px-4 py-3 text-sm font-semibold text-gray-900 group-hover:bg-[#faf8ff]">
                    {app.candidate_name || app.candidate_email}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 break-all">{app.candidate_email}</td>
                  <td className="px-4 py-3 text-sm text-gray-700">{app.candidate_position || '—'}</td>
                  <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                    {typeof app.rank_score === 'number'
                      ? `${app.rank_score.toFixed(1)}/10`
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                    {typeof app.workable_score === 'number'
                      ? `${app.workable_score.toFixed(1)}/10`
                      : '—'}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">
                    {typeof app.cv_match_score === 'number'
                      ? `${app.cv_match_score.toFixed(1)}/10`
                      : (
                        app.cv_filename
                          ? (app.cv_match_details?.error ? 'Unavailable' : 'Pending')
                          : '—'
                      )}
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">{app.source || 'manual'}</td>
                  <td className="px-4 py-3">
                    <Badge variant={statusVariant(app.status)}>{app.status || 'applied'}</Badge>
                  </td>
                  <td className="px-4 py-3 text-sm text-gray-700 whitespace-nowrap">{formatDateTime(app.updated_at || app.created_at)}</td>
                  <td className="min-w-[250px] px-4 py-3">
                    <div className="flex flex-nowrap items-center gap-2">
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
    </div>
  );
};
