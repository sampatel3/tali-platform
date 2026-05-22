import React, { useState } from 'react';
import { Check, Link as LinkIcon } from 'lucide-react';

import { useToast } from '../../context/ToastContext';

// Compact "copy this page's URL" affordance for detail pages. Drops a
// success toast and flips to a check icon for 1.5s so the user sees the
// click registered even if they miss the toast. Pass `href` to copy a
// specific URL; defaults to the current page.
export const CopyLinkButton = ({
  href,
  label = 'Copy link',
  successMessage = 'Link copied.',
  className = '',
  iconOnly = false,
}) => {
  const { showToast } = useToast();
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    const target = href || (typeof window !== 'undefined' ? window.location.href : '');
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target);
      setCopied(true);
      showToast(successMessage, 'success');
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      showToast('Failed to copy link.', 'error');
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`inline-flex items-center gap-1.5 rounded-md border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle,transparent)] px-2 py-1 text-xs text-[var(--taali-muted)] transition-colors hover:text-[var(--taali-text)] hover:bg-[var(--taali-surface-hover,rgba(0,0,0,0.04))] ${className}`.trim()}
      title={label}
      aria-label={label}
    >
      {copied ? <Check size={13} aria-hidden="true" /> : <LinkIcon size={13} aria-hidden="true" />}
      {!iconOnly ? <span>{copied ? 'Copied' : label}</span> : null}
    </button>
  );
};

export default CopyLinkButton;
