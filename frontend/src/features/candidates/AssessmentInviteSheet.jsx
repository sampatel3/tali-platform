import React, { useEffect, useMemo, useState } from 'react';
import { Copy, ExternalLink } from 'lucide-react';

import {
  Button,
  Card,
  Input,
  Sheet,
  Textarea,
} from '../../shared/ui/TaaliPrimitives';

const formatTimestamp = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString();
};

export const AssessmentInviteSheet = ({
  open,
  onClose,
  draft,
}) => {
  const [copyStatus, setCopyStatus] = useState('');

  useEffect(() => {
    if (!open) return;
    setCopyStatus('');
  }, [open, draft?.link]);

  const mailtoHref = useMemo(() => {
    if (!draft?.to) return '';
    const subject = encodeURIComponent(draft.subject || '');
    const body = encodeURIComponent(draft.body || '');
    return `mailto:${draft.to}?subject=${subject}&body=${body}`;
  }, [draft]);

  const copyText = async (label, text) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopyStatus(`${label} copied.`);
      setTimeout(() => setCopyStatus(''), 1800);
    } catch {
      setCopyStatus('Copy failed. Select and copy manually.');
    }
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Send assessment manually"
      description="Copy/paste this email to the candidate. This is useful if automated sending is not configured."
      footer={(
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs text-[var(--taali-muted)]">
            {copyStatus || (draft?.inviteChannel ? `Invite channel: ${draft.inviteChannel}` : '')}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="secondary" onClick={onClose}>Close</Button>
            {mailtoHref ? (
              <Button
                type="button"
                variant="primary"
                onClick={() => window.open(mailtoHref, '_blank', 'noopener,noreferrer')}
              >
                <ExternalLink size={14} />
                Open email client
              </Button>
            ) : null}
          </div>
        </div>
      )}
    >
      {!draft ? (
        <Card className="border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          Invite details unavailable.
        </Card>
      ) : (
        <div className="space-y-4">
          <Card className="bg-[#faf8ff] px-3 py-2 text-sm text-gray-700">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="font-semibold text-gray-900">Candidate</div>
                <div className="text-gray-700">{draft.to}</div>
              </div>
              {draft.inviteSentAt ? (
                <div className="text-xs text-gray-500">
                  Generated {formatTimestamp(draft.inviteSentAt)}
                </div>
              ) : null}
            </div>
          </Card>

          <div className="space-y-2">
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Assessment link
              </span>
              <div className="flex gap-2">
                <Input value={draft.link || ''} readOnly />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => copyText('Link', draft.link || '')}
                  disabled={!draft.link}
                >
                  <Copy size={14} />
                  Copy
                </Button>
              </div>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Subject
              </span>
              <div className="flex gap-2">
                <Input value={draft.subject || ''} readOnly />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => copyText('Subject', draft.subject || '')}
                  disabled={!draft.subject}
                >
                  <Copy size={14} />
                  Copy
                </Button>
              </div>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-[0.08em] text-gray-500">
                Email body
              </span>
              <Textarea
                rows={10}
                value={draft.body || ''}
                readOnly
                className="font-mono text-xs"
              />
              <div className="mt-2 flex justify-end">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => copyText('Email body', draft.body || '')}
                  disabled={!draft.body}
                >
                  <Copy size={14} />
                  Copy body
                </Button>
              </div>
            </label>
          </div>
        </div>
      )}
    </Sheet>
  );
};

