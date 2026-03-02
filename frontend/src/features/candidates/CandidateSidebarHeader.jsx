import React from 'react';

export function CandidateSidebarHeader({ application }) {
  const displayName = application?.candidate_name || application?.candidate_email || 'Candidate';
  const secondaryLine = application?.candidate_headline || application?.candidate_position || application?.role_name || null;
  const phoneNumber = String(application?.candidate_phone || '').trim();
  const initials = displayName
    .split(/\s+/)
    .map((word) => word[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  return (
    <div className="flex items-start gap-3">
      {application?.candidate_image_url ? (
        <img
          src={application.candidate_image_url}
          alt=""
          className="h-10 w-10 shrink-0 rounded-full object-cover"
        />
      ) : (
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--taali-primary)] text-xs font-bold text-white">
          {initials || '?'}
        </div>
      )}
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-lg font-bold tracking-tight text-[var(--taali-text)]">
          {displayName}
        </h2>
        {secondaryLine ? (
          <p className="mt-0.5 truncate text-[13px] text-[var(--taali-muted)]">{secondaryLine}</p>
        ) : null}
        {application?.candidate_email ? (
          <p className="mt-0.5 truncate text-[13px] text-[var(--taali-muted)]">{application.candidate_email}</p>
        ) : null}
        {phoneNumber ? (
          <p className="mt-0.5 truncate text-[13px] text-[var(--taali-muted)]">{phoneNumber}</p>
        ) : null}
      </div>
    </div>
  );
}
