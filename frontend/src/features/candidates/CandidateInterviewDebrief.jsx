import React from 'react';

import { Badge, Button, Panel } from '../../shared/ui/TaaliPrimitives';

const normalizeStatus = (value) => String(value || '').trim().toLowerCase();

const formatDateTime = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toLocaleString();
};

const firefliesStatusMeta = (status) => {
  if (status === 'linked') {
    return { label: 'Stage 1 Fireflies transcript linked', badgeVariant: 'warning' };
  }
  if (status === 'awaiting_transcript') {
    return { label: 'Awaiting Fireflies transcript', badgeVariant: 'info' };
  }
  if (status === 'not_expected') {
    return { label: 'Fireflies capture not expected', badgeVariant: 'muted' };
  }
  return { label: 'Fireflies not configured', badgeVariant: 'muted' };
};

const FirefliesContextPanel = ({ firefliesContext }) => {
  const status = normalizeStatus(firefliesContext?.status);
  const shouldShow = Boolean(
    status === 'linked'
    || status === 'awaiting_transcript'
    || firefliesContext?.capture_expected
    || firefliesContext?.configured
    || firefliesContext?.invite_email
    || firefliesContext?.latest_summary
    || firefliesContext?.latest_provider_url
  );

  if (!shouldShow) {
    return null;
  }

  const { label, badgeVariant } = firefliesStatusMeta(status);
  const inviteEmail = String(firefliesContext?.invite_email || '').trim();
  const latestSummary = String(firefliesContext?.latest_summary || '').trim();
  const latestProviderUrl = String(firefliesContext?.latest_provider_url || '').trim();
  const latestSource = String(firefliesContext?.latest_source || '').trim();
  const meetingDateLabel = formatDateTime(firefliesContext?.latest_meeting_date);

  let description = latestSummary;
  if (!description && status === 'awaiting_transcript') {
    description = inviteEmail
      ? `Include ${inviteEmail} in the Workable interview invite so TAALI can capture the Stage 1 call.`
      : 'Fireflies is configured and TAALI is waiting for the Stage 1 transcript.';
  }
  if (!description && status === 'linked') {
    description = 'The linked Stage 1 transcript is now part of the recruiter guidance context.';
  }
  if (!description && firefliesContext?.capture_expected) {
    description = 'This application is expected to receive Fireflies capture once the Stage 1 interview is workable.';
  }

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Fireflies context</div>
          <div className="mt-2 text-lg font-semibold text-[var(--taali-text)]">{label}</div>
        </div>
        <Badge variant={badgeVariant} className="font-mono text-[11px]">
          {status === 'linked' ? 'Linked' : (firefliesContext?.capture_expected ? 'Workable flow' : 'Optional')}
        </Badge>
      </div>

      {description ? (
        <p className="mt-3 text-sm leading-6 text-[var(--taali-muted)]">{description}</p>
      ) : null}

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Invite email</div>
          <div className="mt-2 text-sm text-[var(--taali-text)]">{inviteEmail || 'Not configured'}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Latest source</div>
          <div className="mt-2 text-sm text-[var(--taali-text)]">{latestSource || 'No transcript linked yet'}</div>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--taali-muted)]">Latest meeting</div>
          <div className="mt-2 text-sm text-[var(--taali-text)]">{meetingDateLabel || 'Not linked yet'}</div>
        </div>
      </div>

      {latestProviderUrl ? (
        <a
          href={latestProviderUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-4 inline-flex text-sm font-medium text-[var(--taali-purple-hover)] underline-offset-2 hover:underline"
        >
          Open transcript source
        </a>
      ) : null}
    </Panel>
  );
};

export const CandidateInterviewDebrief = ({
  debrief,
  loading = false,
  cached = false,
  generatedAt = null,
  onCopyMarkdown = () => {},
  onPrint = () => {},
  onRegenerate = () => {},
}) => {
  if (loading) {
    return (
      <div className="text-sm text-[var(--taali-muted)]">
        Generating interview guide...
      </div>
    );
  }

  if (!debrief) {
    return (
      <Panel className="p-4 text-sm text-[var(--taali-muted)]">
        Interview guide is unavailable.
      </Panel>
    );
  }

  const questions = Array.isArray(debrief.probing_questions) ? debrief.probing_questions : [];
  const strengths = Array.isArray(debrief.strengths_to_validate) ? debrief.strengths_to_validate : [];
  const redFlags = Array.isArray(debrief.red_flags) ? debrief.red_flags : [];
  const generatedAtLabel = generatedAt
    ? new Date(generatedAt).toLocaleString()
    : (debrief.generated_at ? new Date(debrief.generated_at).toLocaleString() : null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={cached ? 'muted' : 'purple'} className="font-mono text-[11px]">
            {cached ? 'Cached' : 'Fresh'}
          </Badge>
          {generatedAtLabel ? (
            <span className="text-xs text-[var(--taali-muted)]">Generated {generatedAtLabel}</span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onCopyMarkdown}>
            Copy as markdown
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={onPrint}>
            Print
          </Button>
          <Button type="button" variant="secondary" size="sm" onClick={onRegenerate}>
            Regenerate
          </Button>
        </div>
      </div>

      <FirefliesContextPanel firefliesContext={debrief.fireflies_context} />

      {debrief.summary ? (
        <Panel className="p-4 bg-[var(--taali-purple-soft)]">
          <div className="text-sm text-[var(--taali-text)]">{debrief.summary}</div>
        </Panel>
      ) : null}

      <div className="space-y-3">
        <h3 className="font-bold text-[var(--taali-text)]">Probing questions</h3>
        {questions.length === 0 ? (
          <Panel className="p-3 text-sm text-[var(--taali-muted)]">
            No questions generated.
          </Panel>
        ) : (
          questions.map((item, index) => (
            <Panel key={`${item.dimension_id || 'q'}-${index}`} className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-semibold text-[var(--taali-text)]">{item.dimension || 'Dimension'}</div>
                {item.score != null ? (
                  <Badge variant="muted" className="font-mono text-[11px]">{item.score}/10</Badge>
                ) : null}
              </div>
              {item.pattern ? (
                <p className="mt-2 text-sm text-[var(--taali-muted)]">{item.pattern}</p>
              ) : null}
              {item.question ? (
                <p className="mt-2 text-sm text-[var(--taali-text)]">
                  <span className="font-semibold">Question:</span> {item.question}
                </p>
              ) : null}
              {item.what_to_listen_for ? (
                <p className="mt-2 text-sm text-[var(--taali-text)]">
                  <span className="font-semibold">What to listen for:</span> {item.what_to_listen_for}
                </p>
              ) : null}
            </Panel>
          ))
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <Panel className="p-4">
          <h4 className="font-semibold text-[var(--taali-text)] mb-2">Strengths to validate</h4>
          {strengths.length === 0 ? (
            <div className="text-sm text-[var(--taali-muted)]">No strengths listed.</div>
          ) : (
            <ul className="space-y-1 text-sm text-[var(--taali-text)]">
              {strengths.map((item, idx) => (
                <li key={`strength-${idx}`}>• {item.text || item.dimension_id}</li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel className="p-4">
          <h4 className="font-semibold text-[var(--taali-text)] mb-2">Red flags to follow up</h4>
          {redFlags.length === 0 ? (
            <div className="text-sm text-[var(--taali-muted)]">No red flags detected.</div>
          ) : (
            <ul className="space-y-2 text-sm text-[var(--taali-text)]">
              {redFlags.map((item, idx) => (
                <li key={`flag-${idx}`}>
                  • {item.text || item.dimension_id}
                  {item.follow_up_question ? (
                    <div className="text-xs text-[var(--taali-muted)] mt-1">Follow-up: {item.follow_up_question}</div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
};
