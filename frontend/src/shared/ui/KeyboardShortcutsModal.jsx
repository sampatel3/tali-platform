import React, { useEffect } from 'react';
import { X } from 'lucide-react';

import { Button } from './TaaliPrimitives';

const SHORTCUTS = [
  { keys: ['⌘', 'K'], description: 'Open global search' },
  { keys: ['/'], description: 'Focus search bar' },
  { keys: ['?'], description: 'Show this help' },
  { keys: ['Esc'], description: 'Close modal / dropdown' },
];

// Lightweight overlay listing the platform's keyboard shortcuts.
// Click backdrop or press Escape (handled by the parent) to dismiss.
export const KeyboardShortcutsModal = ({ open, onClose }) => {
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="kbd-shortcuts-title"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.45)',
        zIndex: 9000,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        padding: '12vh 16px 16px',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--taali-card-bg, #fff)',
          color: 'var(--ink)',
          borderRadius: 12,
          boxShadow: 'var(--taali-shadow-strong, 0 12px 32px rgba(0,0,0,0.18))',
          width: '100%',
          maxWidth: 420,
          padding: '20px 22px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <h2 id="kbd-shortcuts-title" style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>
            Keyboard shortcuts
          </h2>
          <Button
            variant="ghost"
            size="xs"
            iconOnly
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </Button>
        </div>
        <dl style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.description} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <dt style={{ fontSize: 13, color: 'var(--ink-2, #555)' }}>{shortcut.description}</dt>
              <dd style={{ margin: 0, display: 'flex', gap: 4 }}>
                {shortcut.keys.map((key, i) => (
                  <kbd
                    key={`${shortcut.description}-${i}`}
                    style={{
                      display: 'inline-block',
                      minWidth: 22,
                      padding: '2px 6px',
                      borderRadius: 4,
                      border: '1px solid var(--taali-border-soft, #ddd)',
                      background: 'var(--taali-surface-subtle, #f6f6f6)',
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                      fontSize: 11,
                      lineHeight: 1.4,
                      textAlign: 'center',
                    }}
                  >
                    {key}
                  </kbd>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
};

export default KeyboardShortcutsModal;
