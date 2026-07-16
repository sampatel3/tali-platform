import React from 'react';
import { ClipboardList, Gauge } from 'lucide-react';

import { ChatArtifact } from '../../shared/chat';

const humanize = (value) => String(value || '').replace(/_/g, ' ');

const Count = ({ label, value, alert = false }) => (
  <div className={['cp-op-stat', alert ? 'is-alert' : ''].join(' ')}>
    <strong>{Number(value || 0).toLocaleString()}</strong>
    <span>{label}</span>
  </div>
);

export const RecruitingOverviewCard = ({ data }) => {
  if (!data) return null;
  const assessments = data.assessments || {};
  const applications = data.applications || {};
  const scope = data.scope || {};
  const stageEntries = Object.entries(applications.pipeline_stages || {}).filter(
    ([, value]) => Number(value) > 0,
  );
  const footer = data.frontend_url ? (
    <a className="cp-artifact-link" href={data.frontend_url} target="_blank" rel="noopener noreferrer">
      Open dashboard <span aria-hidden="true">↗</span>
    </a>
  ) : null;
  return (
    <ChatArtifact
      aria-label="Recruiting overview"
      eyebrow="Live overview"
      title={scope.role_name || 'Recruiting operations'}
      summary="Current pipeline and assessment workload"
      icon={Gauge}
      footer={footer}
    >
      <div className="cp-op-stats">
        <Count label="roles" value={data.roles?.total} />
        <Count label="candidates" value={data.candidates?.total} />
        <Count label="applications" value={applications.total} />
        <Count label="assessments" value={assessments.total} />
        <Count
          label="need attention"
          value={assessments.needs_attention}
          alert={Number(assessments.needs_attention) > 0}
        />
      </div>
      {stageEntries.length ? (
        <div className="cp-op-pills" aria-label="Application pipeline">
          {stageEntries.map(([stage, value]) => (
            <span key={stage}>{humanize(stage)} · {value}</span>
          ))}
        </div>
      ) : null}
    </ChatArtifact>
  );
};

export const AssessmentQueueCard = ({ data }) => {
  if (!data) return null;
  const rows = Array.isArray(data.items) ? data.items : [];
  const footer = data.frontend_url ? (
    <a className="cp-artifact-link" href={data.frontend_url} target="_blank" rel="noopener noreferrer">
      View all assessments <span aria-hidden="true">↗</span>
    </a>
  ) : null;
  return (
    <ChatArtifact
      aria-label="Assessment work queue"
      eyebrow="Assessment queue"
      title={`${data.total ?? rows.length} matching`}
      summary="Candidates and submissions that match this request"
      icon={ClipboardList}
      footer={footer}
    >
      {rows.length ? (
        <div className="cp-assessment-list">
          {rows.map((row) => (
            <a
              className="cp-assessment-row"
              href={row.frontend_url || '#'}
              target="_blank"
              rel="noopener noreferrer"
              key={row.assessment_id}
            >
              <div>
                <strong>{row.candidate_name || 'Unknown candidate'}</strong>
                <span>{[row.role_name, row.task_name].filter(Boolean).join(' · ')}</span>
              </div>
              <div className="cp-assessment-meta">
                {row.score_100 != null ? <span>{Math.round(row.score_100)} score</span> : null}
                <span>{humanize(row.status)}</span>
                {row.attention_required ? (
                  <span className="is-alert">
                    {row.attention_reasons?.map(humanize).join(' · ') || 'needs attention'}
                  </span>
                ) : null}
              </div>
            </a>
          ))}
        </div>
      ) : (
        <p className="cp-op-empty">No assessments matched.</p>
      )}
    </ChatArtifact>
  );
};
