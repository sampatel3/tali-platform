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

const COLUMN_STORAGE_KEY = 'taali_candidates_table_columns_v1';

const DEFAULT_COLUMN_PREFS = {
  workable_ai: true,
  workable_raw: false,
  workable_score_source: false,
  workable_stage: true,
  workable_candidate_id: false,
  added: true,
  email: false,
  position: false,
  source: false,
};

export const CandidatesTable = ({
  applications,
  loading,
  error,
  searchQuery = '',
  statusFilter = 'all',
  sortBy = 'workable_score',
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
  const [detailsApplicationId, setDetailsApplicationId] = useState(null);
  const [taskByApplication, setTaskByApplication] = useState({});
  const [columnsOpen, setColumnsOpen] = useState(false);
  const [columnPrefs, setColumnPrefs] = useState(() => {
    try {
      if (typeof window === 'undefined') return DEFAULT_COLUMN_PREFS;
      const raw = window.localStorage.getItem(COLUMN_STORAGE_KEY);
      if (!raw) return DEFAULT_COLUMN_PREFS;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return DEFAULT_COLUMN_PREFS;
      return { ...DEFAULT_COLUMN_PREFS, ...parsed };
    } catch {
      return DEFAULT_COLUMN_PREFS;
    }
  });

  const sortableColumns = {
    workable_score: 'Workable AI',
    cv_match_score: 'Taali AI',
    created_at: 'Added',
  };

  useEffect(() => {
    setComposerApplicationId(null);
    setDetailsApplicationId(null);
  }, [applications, roleTasks]);

  useEffect(() => {
    try {
      if (typeof window === 'undefined') return;
      window.localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(columnPrefs));
    } catch {
      // ignore persistence failures (e.g. private browsing)
    }
  }, [columnPrefs]);

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

  const showColumn = (key) => {
    if (key === 'candidate') return true;
    if (key === 'taali_ai') return true;
    if (key === 'send') return true;
    if (key === 'status') return true;
    return Boolean(columnPrefs[key]);
  };

  const visibleColumnOrder = useMemo(() => (
    [
      'candidate',
      'send',
      'taali_ai',
      'workable_ai',
      'workable_raw',
      'workable_score_source',
      'workable_stage',
      'workable_candidate_id',
      'status',
      'added',
      'email',
      'position',
      'source',
    ].filter(showColumn)
  ), [columnPrefs]);

  const columnCount = visibleColumnOrder.length;

  const togglePref = (key) => {
    setColumnPrefs((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const formatScore = (value) => (
    typeof value === 'number'
      ? `${value.toFixed(1)}/10`
      : '—'
  );

  const formatWorkableRaw = (value) => {
    if (typeof value !== 'number') return '—';
    if (Number.isInteger(value)) return String(value);
    return value.toFixed(2);
  };

  const renderTaaliScore = (app) => {
    if (typeof app.cv_match_score === 'number') return formatScore(app.cv_match_score);
    if (!app.cv_filename) return '—';
    if (app.cv_match_details?.error) return 'Unavailable';
    return 'Pending';
  };

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
      <div className="relative flex flex-wrap items-center justify-between gap-2 px-1">
        <p className="text-xs text-gray-500">
          {filtered.length}
          {' '}
          candidate{filtered.length === 1 ? '' : 's'}
        </p>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setColumnsOpen((prev) => !prev)}
          >
            Columns
          </Button>
        </div>
        {columnsOpen ? (
          <div className="absolute right-1 top-full z-40 mt-2 w-[280px] border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-3 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
              Show columns
            </p>
            <div className="mt-2 grid gap-2">
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.workable_ai)}
                  onChange={() => togglePref('workable_ai')}
                />
                Workable AI score
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.workable_raw)}
                  onChange={() => togglePref('workable_raw')}
                />
                Workable raw score
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.workable_score_source)}
                  onChange={() => togglePref('workable_score_source')}
                />
                Workable score source
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.workable_stage)}
                  onChange={() => togglePref('workable_stage')}
                />
                Workable stage
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.workable_candidate_id)}
                  onChange={() => togglePref('workable_candidate_id')}
                />
                Workable candidate id
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.added)}
                  onChange={() => togglePref('added')}
                />
                Added date
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.email)}
                  onChange={() => togglePref('email')}
                />
                Email
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.position)}
                  onChange={() => togglePref('position')}
                />
                Position
              </label>
              <label className="flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox"
                  checked={Boolean(columnPrefs.source)}
                  onChange={() => togglePref('source')}
                />
                Source
              </label>
            </div>
            <div className="mt-3 flex justify-end">
              <Button type="button" variant="secondary" size="sm" onClick={() => setColumnsOpen(false)}>
                Close
              </Button>
            </div>
          </div>
        ) : null}
      </div>
      <TableShell className="max-h-[68vh]">
        <table className="w-full table-fixed min-w-[900px]">
          <thead>
          <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
            {visibleColumnOrder.map((column) => {
              const isStickyLeft = column === 'candidate';
              const thBase = isStickyLeft
                ? 'sticky left-0 top-0 z-30 bg-[#f4f1fb]'
                : 'sticky top-0 z-20 bg-[#f4f1fb]';
              const widthClass = {
                candidate: 'w-[240px]',
                taali_ai: 'w-[110px]',
                send: 'w-[170px]',
                workable_ai: 'w-[110px]',
                workable_raw: 'w-[120px]',
                workable_score_source: 'w-[220px]',
                workable_stage: 'w-[150px]',
                workable_candidate_id: 'w-[200px]',
                status: 'w-[140px]',
                added: 'w-[140px]',
                email: 'w-[220px]',
                position: 'w-[200px]',
                source: 'w-[120px]',
              }[column] || '';

              if (column === 'candidate') {
                return (
                  <th key={column} className={`${thBase} ${widthClass} px-3 py-2`}>Candidate</th>
                );
              }

              if (column === 'taali_ai') {
                return (
                  <th
                    key={column}
                    className={`${thBase} ${widthClass} px-3 py-2`}
                    aria-sort={sortBy === 'cv_match_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                      onClick={() => handleSortToggle('cv_match_score')}
                    >
                      Taali AI
                      <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('cv_match_score')}</span>
                    </button>
                  </th>
                );
              }

              if (column === 'workable_ai') {
                return (
                  <th
                    key={column}
                    className={`${thBase} ${widthClass} px-3 py-2`}
                    aria-sort={sortBy === 'workable_score' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                      onClick={() => handleSortToggle('workable_score')}
                    >
                      Workable AI
                      <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('workable_score')}</span>
                    </button>
                  </th>
                );
              }

              if (column === 'added') {
                return (
                  <th
                    key={column}
                    className={`${thBase} ${widthClass} px-3 py-2`}
                    aria-sort={sortBy === 'created_at' ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                  >
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 uppercase tracking-[0.08em] text-gray-600 transition-colors hover:text-gray-900"
                      onClick={() => handleSortToggle('created_at')}
                    >
                      Added
                      <span className="text-[0.65rem] text-gray-500">{renderSortIndicator('created_at')}</span>
                    </button>
                  </th>
                );
              }

              const label = {
                send: 'Send assessment',
                workable_raw: 'Workable raw',
                workable_score_source: 'Workable score src',
                workable_stage: 'Workable stage',
                workable_candidate_id: 'Workable id',
                status: 'Status',
                email: 'Email',
                position: 'Position',
                source: 'Source',
              }[column] || column;

              return (
                <th key={column} className={`${thBase} ${widthClass} px-3 py-2`}>{label}</th>
              );
            })}
          </tr>
          </thead>
          <tbody>
          {filtered.map((app) => {
            const selectedTask = taskByApplication[app.id] || (roleTasks.length === 1 ? String(roleTasks[0].id) : '');
            const canOpenComposer = Boolean(canCreateAssessment && app.cv_filename && roleTasks.length > 0);

            return (
              <React.Fragment key={app.id}>
                <tr className="group align-top">
                  {visibleColumnOrder.map((column) => {
                    if (column === 'candidate') {
                      return (
                        <td
                          key={column}
                          className="sticky left-0 z-10 bg-[var(--taali-surface)] px-3 py-2 text-sm group-hover:bg-[#faf8ff]"
                        >
                          <button
                            type="button"
                            className="block w-full text-left font-semibold text-gray-900 hover:underline"
                            onClick={() => onViewCandidate(app)}
                            disabled={viewingApplicationId === app.id}
                          >
                            {app.candidate_name || app.candidate_email}
                          </button>
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              className="text-xs text-gray-500 underline decoration-gray-300 underline-offset-2 hover:text-gray-800"
                              onClick={() => {
                                setDetailsApplicationId((current) => (current === app.id ? null : app.id));
                              }}
                            >
                              {detailsApplicationId === app.id ? 'Hide details' : 'Details'}
                            </button>
                            {app.source === 'workable' ? (
                              <span className="text-xs text-gray-500">Workable</span>
                            ) : null}
                          </div>
                        </td>
                      );
                    }

                    if (column === 'taali_ai') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 text-right tabular-nums whitespace-nowrap">
                          {renderTaaliScore(app)}
                        </td>
                      );
                    }

                    if (column === 'send') {
                      return (
                        <td key={column} className="px-3 py-2">
                          {canCreateAssessment ? (
                            <div className="flex flex-col items-start gap-1">
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
                                className="whitespace-nowrap"
                              >
                                Send assessment
                              </Button>
                              {!app.cv_filename ? (
                                <span className="text-[0.7rem] font-semibold text-amber-700">CV required</span>
                              ) : null}
                              {app.cv_filename && roleTasks.length === 0 ? (
                                <span className="text-[0.7rem] font-semibold text-amber-700">Link a task first</span>
                              ) : null}
                            </div>
                          ) : (
                            <span className="text-sm text-gray-500">—</span>
                          )}
                        </td>
                      );
                    }

                    if (column === 'workable_ai') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 text-right tabular-nums whitespace-nowrap">
                          {formatScore(app.workable_score)}
                        </td>
                      );
                    }

                    if (column === 'workable_raw') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 text-right tabular-nums whitespace-nowrap">
                          {formatWorkableRaw(app.workable_score_raw)}
                        </td>
                      );
                    }

                    if (column === 'workable_score_source') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-words">
                          {app.workable_score_source || '—'}
                        </td>
                      );
                    }

                    if (column === 'workable_stage') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-words">
                          {app.workable_stage || '—'}
                        </td>
                      );
                    }

                    if (column === 'workable_candidate_id') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-all">
                          {app.workable_candidate_id || '—'}
                        </td>
                      );
                    }

                    if (column === 'status') {
                      return (
                        <td key={column} className="px-3 py-2">
                          <Badge variant={statusVariant(app.status)}>{app.status || 'applied'}</Badge>
                        </td>
                      );
                    }

                    if (column === 'added') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 whitespace-nowrap">
                          {formatDateTime(app.created_at)}
                        </td>
                      );
                    }

                    if (column === 'email') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-all">
                          {app.candidate_email || '—'}
                        </td>
                      );
                    }

                    if (column === 'position') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 break-words">
                          {app.candidate_position || '—'}
                        </td>
                      );
                    }

                    if (column === 'source') {
                      return (
                        <td key={column} className="px-3 py-2 text-sm text-gray-700 whitespace-nowrap">
                          {app.source || 'manual'}
                        </td>
                      );
                    }

                    return null;
                  })}
                </tr>

                {composerApplicationId === app.id ? (
                  <tr className="bg-[#faf8ff]">
                    <td colSpan={columnCount} className="px-3 py-3">
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

                {detailsApplicationId === app.id ? (
                  <tr className="bg-[#faf8ff]">
                    <td colSpan={columnCount} className="px-3 py-3">
                      <p className="mb-3 text-xs text-gray-500">
                        Status is TAALI&apos;s application status; Workable stage is the pipeline step from Workable. For Workable-imported candidates they may match.
                      </p>
                      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Status</p>
                          <p className="mt-1 text-sm text-gray-800">{app.status || 'applied'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Email</p>
                          <p className="mt-1 text-sm text-gray-800 break-all">{app.candidate_email || '—'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Position</p>
                          <p className="mt-1 text-sm text-gray-800 break-words">{app.candidate_position || '—'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Source</p>
                          <p className="mt-1 text-sm text-gray-800">{app.source || 'manual'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Workable candidate id</p>
                          <p className="mt-1 text-sm text-gray-800 break-all">{app.workable_candidate_id || '—'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Workable stage</p>
                          <p className="mt-1 text-sm text-gray-800 break-words">{app.workable_stage || '—'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Workable score</p>
                          <p className="mt-1 text-sm text-gray-800">
                            {typeof app.workable_score === 'number' ? formatScore(app.workable_score) : '—'}
                            {typeof app.workable_score_raw === 'number' ? ` (raw: ${formatWorkableRaw(app.workable_score_raw)})` : ''}
                          </p>
                          {app.workable_score_source ? (
                            <p className="mt-1 text-xs text-gray-500 break-all">{app.workable_score_source}</p>
                          ) : null}
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Taali score</p>
                          <p className="mt-1 text-sm text-gray-800">{renderTaaliScore(app)}</p>
                          {app.cv_match_details?.error ? (
                            <p className="mt-1 text-xs text-amber-700">{app.cv_match_details.error}</p>
                          ) : null}
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">CV</p>
                          <p className="mt-1 text-sm text-gray-800">{app.cv_filename || '—'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Added</p>
                          <p className="mt-1 text-sm text-gray-800">{formatDateTime(app.created_at)}</p>
                        </div>
                        <div>
                          <p className="text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">Updated</p>
                          <p className="mt-1 text-sm text-gray-800">{formatDateTime(app.updated_at || app.created_at)}</p>
                        </div>
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
