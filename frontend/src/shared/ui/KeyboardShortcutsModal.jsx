import React from 'react';

import { Dialog } from './TaaliPrimitives';

const SHORTCUTS = [
  { keys: ['⌘', 'K'], description: 'Open global search' },
  { keys: ['/'], description: 'Focus search bar' },
  { keys: ['?'], description: 'Show this help' },
  { keys: ['Esc'], description: 'Close modal / dropdown' },
];

// Lightweight overlay listing the platform's keyboard shortcuts.
// Click backdrop or press Escape (handled by the parent) to dismiss.
export const KeyboardShortcutsModal = ({ open, onClose }) => {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Keyboard shortcuts"
      panelClassName="max-w-[26.25rem]"
    >
      <dl className="m-0 flex flex-col gap-2.5">
        {SHORTCUTS.map((shortcut) => (
          <div key={shortcut.description} className="flex items-center justify-between gap-3">
            <dt className="text-[0.8125rem] text-[var(--ink-2)]">{shortcut.description}</dt>
            <dd className="m-0 flex gap-1">
              {shortcut.keys.map((key, index) => (
                <kbd
                  key={`${shortcut.description}-${index}`}
                  className="inline-block min-w-[1.375rem] rounded border border-[var(--taali-border-soft)] bg-[var(--taali-surface-subtle)] px-1.5 py-0.5 text-center font-mono text-[0.6875rem] leading-[1.4]"
                >
                  {key}
                </kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </Dialog>
  );
};

export default KeyboardShortcutsModal;
