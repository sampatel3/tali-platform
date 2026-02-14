import React from 'react';

import { Button, Panel, TableShell } from '../../shared/ui/TaaliPrimitives';

export const LegacyCandidatesPanel = ({
  loading,
  error,
  legacyCandidates,
  onViewCandidate,
  mapAssessmentForDetail,
}) => (
  <Panel className="p-4">
    <div className="mb-3">
      <h3 className="text-sm font-semibold uppercase tracking-[0.08em] text-gray-600">
        Dashboard candidates
      </h3>
      <p className="mt-1 text-sm text-gray-600">
        Candidates that exist from the assessment flow but are not attached to a role application.
      </p>
    </div>
    {loading ? (
      <p className="text-sm text-gray-500">Loading dashboard candidates...</p>
    ) : null}
    {!loading && error ? (
      <p className="text-sm text-red-700">{error}</p>
    ) : null}
    {!loading && !error && legacyCandidates.length === 0 ? (
      <p className="text-sm text-gray-500">No dashboard-only candidates found.</p>
    ) : null}
    {!loading && !error && legacyCandidates.length > 0 ? (
      <TableShell>
        <table className="min-w-[720px]">
          <thead>
            <tr className="text-left text-xs font-semibold uppercase tracking-[0.08em] text-gray-600">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Role</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Last activity</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody>
            {legacyCandidates.map((assessment) => (
              <tr key={assessment.id}>
                <td className="px-4 py-3 text-sm font-semibold text-gray-900">
                  {(assessment.candidate_name || assessment.candidate_email || 'Unknown').trim()}
                </td>
                <td className="px-4 py-3 text-sm text-gray-700">{assessment.candidate_email || '—'}</td>
                <td className="px-4 py-3 text-sm text-gray-700">{assessment.role_name || 'Unassigned role'}</td>
                <td className="px-4 py-3 text-sm text-gray-700">{assessment.status || 'pending'}</td>
                <td className="px-4 py-3 text-sm text-gray-700">
                  {assessment.updated_at
                    ? new Date(assessment.updated_at).toLocaleString()
                    : (assessment.created_at ? new Date(assessment.created_at).toLocaleString() : '—')}
                </td>
                <td className="px-4 py-3">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => onViewCandidate(mapAssessmentForDetail(assessment))}
                  >
                    View
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableShell>
    ) : null}
  </Panel>
);
