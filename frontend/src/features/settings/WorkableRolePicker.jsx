import React, { useMemo } from 'react';

const jobIdentifier = (job) => String(job?.shortcode || job?.id || '').trim();

export const WorkableRolePicker = ({
  canManage,
  jobs,
  loading,
  error,
  search,
  onSearchChange,
  selected,
  onSelectedChange,
}) => {
  const filteredJobs = useMemo(() => {
    const query = String(search || '').trim().toLowerCase();
    if (!query) return jobs;
    return jobs.filter((job) => (
      jobIdentifier(job).toLowerCase().includes(query)
      || String(job?.title || '').toLowerCase().includes(query)
    ));
  }, [jobs, search]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  return (
    <div className="settings-role-picker settings-top-gap">
      <div className="settings-role-picker-header">
        <div>
          <div className="settings-summary-label">Roles to import</div>
          <div className="settings-summary-note">{selected.length}/{jobs.length} selected</div>
        </div>
        <div className="settings-inline-actions">
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onSelectedChange(jobs.map(jobIdentifier).filter(Boolean))}
            disabled={!canManage || loading || jobs.length === 0}
          >
            Select all
          </button>
          <button
            type="button"
            className="btn btn-outline btn-sm"
            onClick={() => onSelectedChange([])}
            disabled={!canManage || loading || selected.length === 0}
          >
            Clear
          </button>
        </div>
      </div>
      <input
        className="settings-search-input"
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
        placeholder="Search role name or shortcode"
      />
      {error ? <div className="settings-error-copy">{error}</div> : null}
      <div className="settings-role-picker-list">
        {loading ? (
          <div className="settings-empty-state">Loading Workable roles...</div>
        ) : filteredJobs.length === 0 ? (
          <div className="settings-empty-state">No roles match your search.</div>
        ) : filteredJobs.map((job) => {
          const identifier = jobIdentifier(job);
          if (!identifier) return null;
          return (
            <label key={identifier} className="settings-scope-item">
              <input
                type="checkbox"
                checked={selectedSet.has(identifier)}
                disabled={!canManage}
                onChange={() => onSelectedChange(
                  selected.includes(identifier)
                    ? selected.filter((item) => item !== identifier)
                    : [...selected, identifier],
                )}
              />
              <span><b>{job?.title || identifier}</b><small>{identifier}</small></span>
            </label>
          );
        })}
      </div>
    </div>
  );
};

export default WorkableRolePicker;
