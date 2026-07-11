import React from 'react';

const SCORE_LABELS = {
  taali: 'Taali',
  pre_screen: 'Pre-screen',
  rank: 'Rank',
  cv_match: 'CV match',
  workable: 'Workable',
  assessment: 'Assessment',
  role_fit: 'Role fit',
};

// Displays the result of `compare_applications`. Highlights the highest
// score per row with a soft-purple wash (in-scheme — no traffic-light
// green) so a recruiter can pick out leaders fast.
const ComparisonTable = ({ payload }) => {
  const apps = payload?.applications || [];
  if (!apps.length) return null;

  const scoreKeys = ['taali', 'pre_screen', 'rank', 'cv_match', 'workable', 'assessment', 'role_fit'];
  const present = scoreKeys.filter((key) =>
    apps.some((a) => a.scores && a.scores[key] != null),
  );

  const bestForRow = (key) => {
    let best = null;
    for (const a of apps) {
      const v = a.scores?.[key];
      if (v == null) continue;
      if (best == null || v > best) best = v;
    }
    return best;
  };

  return (
    <div className="cp-compare">
      <table>
        <thead>
          <tr>
            <th>candidate</th>
            {apps.map((a) => (
              <th key={a.application_id}>
                <a href={a.frontend_url} target="_blank" rel="noopener noreferrer">
                  {a.candidate_name || `app ${a.application_id}`}
                </a>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>stage</td>
            {apps.map((a) => (
              <td key={a.application_id}>{a.pipeline_stage || '—'}</td>
            ))}
          </tr>
          {present.map((key) => {
            const best = bestForRow(key);
            return (
              <tr key={key}>
                <td>{SCORE_LABELS[key] || key}</td>
                {apps.map((a) => {
                  const v = a.scores?.[key];
                  const isBest = v != null && v === best && apps.length > 1;
                  return (
                    <td key={a.application_id} className={isBest ? 'cp-best' : ''}>
                      {v == null ? '—' : Math.round(v)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default ComparisonTable;
