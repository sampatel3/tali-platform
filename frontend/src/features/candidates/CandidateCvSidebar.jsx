import React from 'react';
import {
  ExternalLink,
  FileText,
  Github,
  Linkedin,
  Loader2,
  MapPin,
  Twitter,
} from 'lucide-react';

import { Badge, Button, Panel, Sheet } from '../../shared/ui/TaaliPrimitives';
import { CandidateScoreRing } from './CandidateScoreRing';
import { CandidateSidebarHeader } from './CandidateSidebarHeader';
import {
  buildApplicationStatusMeta,
  formatCvScore100,
  formatDateTime,
} from './candidatesUiUtils';

const SOCIAL_ICONS = {
  linkedin: Linkedin,
  github: Github,
  twitter: Twitter,
};

const CV_SECTION_HEADERS = /^(Professional\s+)?Experience|Work\s+(?:History|Experience)|Education|Skills|Summary|Objective|Qualifications|Certifications|Projects|Achievements|Languages$/i;

const modeMeta = (mode) => {
  if (mode === 'assessment_plus_cv') return { label: 'Assessment + CV', variant: 'purple' };
  if (mode === 'assessment_only_fallback') return { label: 'Assessment only', variant: 'warning' };
  if (mode === 'pending') return { label: 'Pending', variant: 'muted' };
  return { label: 'CV fit only', variant: 'muted' };
};

function formatCvWithSections(text) {
  if (!text || typeof text !== 'string') return null;
  const trimmed = text.trim();
  if (!trimmed) return null;
  const lines = trimmed.split(/\n/);
  const elements = [];
  let currentBlock = [];
  const flushBlock = (isHeader = false) => {
    const block = currentBlock.join('\n').trim();
    if (block) {
      if (isHeader) {
        elements.push(
          <h4 key={elements.length} className="mb-2 mt-4 border-b border-[var(--taali-border-muted)] pb-1 text-sm font-semibold text-[var(--taali-text)] first:mt-0">
            {block}
          </h4>
        );
      } else {
        elements.push(
          <div key={elements.length} className="whitespace-pre-wrap text-sm leading-relaxed text-[var(--taali-text)]">
            {block}
          </div>
        );
      }
    }
    currentBlock = [];
  };
  for (const line of lines) {
    const trimmedLine = line.trim();
    if (CV_SECTION_HEADERS.test(trimmedLine)) {
      flushBlock(false);
      currentBlock.push(line);
      flushBlock(true);
    } else {
      currentBlock.push(line);
    }
  }
  flushBlock(false);
  if (elements.length === 0) return <span className="block whitespace-pre-wrap">{trimmed}</span>;
  return <div className="space-y-2">{elements}</div>;
}

const InfoCard = ({ label, value }) => (
  <div className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] px-3 py-3">
    <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">{label}</p>
    <p className="mt-2 text-sm font-semibold text-[var(--taali-text)]">{value}</p>
  </div>
);

export function CandidateCvSidebar({ open, application, onClose, onFetchCvFromWorkable, fetchingCvApplicationId }) {
  const data = application ?? null;
  const socials = Array.isArray(data?.candidate_social_profiles) ? data.candidate_social_profiles : [];
  const skills = Array.isArray(data?.candidate_skills) ? data.candidate_skills : [];
  const statusMeta = buildApplicationStatusMeta(data?.status, data?.workable_stage);
  const taaliScore = data?.score_summary?.taali_score ?? data?.taali_score ?? data?.cv_match_score ?? null;
  const taaliScoreDetails = data?.score_summary?.taali_score != null || data?.taali_score != null
    ? { score_scale: '0-100' }
    : data?.cv_match_details;
  const mode = modeMeta(data?.score_mode || data?.score_summary?.mode);

  const footer = data?.cv_text ? (
    <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-[var(--taali-muted)]">
      <span>CV loaded for review.</span>
      {data.cv_filename ? <span className="font-mono text-xs">{data.cv_filename}</span> : null}
    </div>
  ) : data ? (
    <div className="space-y-2">
      {data.source === 'workable' && onFetchCvFromWorkable ? (
        <>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={fetchingCvApplicationId === data.id}
            onClick={() => onFetchCvFromWorkable(data)}
          >
            {fetchingCvApplicationId === data.id ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                Fetching from Workable...
              </>
            ) : (
              'Fetch CV from Workable'
            )}
          </Button>
          <p className="text-xs text-[var(--taali-muted)]">
            Downloads the resume from Workable, extracts text, and refreshes the role-fit scoring.
          </p>
        </>
      ) : (
        <p className="text-xs text-[var(--taali-muted)]">
          Upload a CV for this application to view the parsed resume here.
        </p>
      )}
    </div>
  ) : null;

  return (
    <Sheet
      open={open}
      onClose={onClose}
      side="left"
      title={data?.candidate_name || data?.candidate_email || 'Candidate CV'}
      description={data?.role_name || data?.candidate_position || 'Candidate CV'}
      headerContent={<CandidateSidebarHeader application={data} />}
      footer={footer}
    >
      {!data ? (
        <Panel className="p-4 text-sm text-[var(--taali-muted)]">Candidate details unavailable.</Panel>
      ) : (
        <div className="space-y-4">
          <Panel className="overflow-hidden border-2 border-[var(--taali-border)] bg-[linear-gradient(135deg,rgba(190,171,255,0.16),rgba(255,255,255,0.98))] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-4">
                <CandidateScoreRing
                  score={taaliScore}
                  details={taaliScoreDetails}
                  size={96}
                  strokeWidth={9}
                  label={`TAALI Score for ${data.candidate_name || data.candidate_email || 'candidate'}`}
                  valueClassName="text-[1.45rem]"
                />
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">TAALI Score</p>
                  <p className="mt-2 font-mono text-3xl font-bold text-[var(--taali-text)]">
                    {formatCvScore100(taaliScore, taaliScoreDetails)}
                  </p>
                  <p className="mt-2 text-sm text-[var(--taali-muted)]">
                    {data.role_name || data.candidate_position || 'Candidate CV review'}
                  </p>
                </div>
              </div>
              <div className="space-y-3 sm:text-right">
                <Badge variant={mode.variant}>{mode.label}</Badge>
                <p className="text-xs text-[var(--taali-muted)]">Updated {formatDateTime(data.updated_at || data.created_at)}</p>
              </div>
            </div>
          </Panel>

          <Panel className="p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              {statusMeta.map((item) => (
                <InfoCard key={item.label} label={item.label} value={item.value} />
              ))}
              {data.candidate_location ? (
                <InfoCard label="Location" value={data.candidate_location} />
              ) : null}
              {data.candidate_phone ? (
                <InfoCard label="Phone" value={data.candidate_phone} />
              ) : null}
            </div>
            {socials.length > 0 ? (
              <div className="mt-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Profiles</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {socials.map((profile, index) => {
                    const type = (profile.type || '').toLowerCase();
                    const Icon = SOCIAL_ICONS[type] || ExternalLink;
                    return (
                      <a
                        key={`${type}-${index}`}
                        href={profile.url || '#'}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 border border-[var(--taali-border-muted)] px-3 py-2 text-sm text-[var(--taali-text)] transition-colors hover:bg-[var(--taali-surface-subtle)]"
                      >
                        <Icon size={14} />
                        {profile.name || type || 'Profile'}
                      </a>
                    );
                  })}
                </div>
              </div>
            ) : null}
            {skills.length > 0 ? (
              <div className="mt-4">
                <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Skills</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {skills.map((skill) => (
                    <Badge key={skill} variant="muted">{skill}</Badge>
                  ))}
                </div>
              </div>
            ) : null}
          </Panel>

          <Panel className="p-4">
            <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">
              <FileText size={14} />
              CV
            </div>
            {data.cv_text ? (
              <div className="border border-[var(--taali-border-muted)] bg-[var(--taali-surface-subtle)] p-4">
                {formatCvWithSections(data.cv_text)}
              </div>
            ) : (
              <div className="border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                No CV text is available for this candidate yet.
                {data.candidate_location ? (
                  <div className="mt-2 inline-flex items-center gap-1.5 text-xs text-amber-700">
                    <MapPin size={12} />
                    {data.candidate_location}
                  </div>
                ) : null}
              </div>
            )}
          </Panel>
        </div>
      )}
    </Sheet>
  );
}
