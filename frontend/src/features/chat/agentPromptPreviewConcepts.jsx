import { ListChecks, MessageCircle, PanelTop, Send } from 'lucide-react';

export const DEFAULT_AGENT_PROMPT_VARIANT = 'a';

export const AGENT_PROMPT_CONCEPTS = Object.freeze({
  a: {
    id: 'a',
    number: '01',
    name: 'Conversation turn',
    short: 'Inline',
    icon: MessageCircle,
    thesis: 'Make the request feel like dialogue, not a notification card.',
    bestFor: 'Everyday questions and one-to-three quick choices.',
    watchOut: 'Important blockers can scroll out of view in a long thread.',
    motion: ['Message rises 4px', 'Actions stagger 35ms', 'Answer morphs to receipt'],
    verdict: 'Best default',
  },
  b: {
    id: 'b',
    number: '02',
    name: 'Needs-you tray',
    short: 'Pinned tray',
    icon: PanelTop,
    thesis: 'Keep one genuine blocker visible without turning the transcript into an alert feed.',
    bestFor: 'Multiple unresolved blockers and time-sensitive decisions.',
    watchOut: 'Too heavy for suggestions; reserve it for work that is truly paused.',
    motion: ['Tray enters from header', 'Prompt swaps horizontally', 'Final answer collapses tray'],
    verdict: 'Best for blockers',
  },
  c: {
    id: 'c',
    number: '03',
    name: 'Composer reply mode',
    short: 'Composer',
    icon: Send,
    thesis: 'Turn the composer into the answer control so the whole workflow stays chat-native.',
    bestFor: 'Typed answers, quick replies, and the end-to-end chat vision.',
    watchOut: 'Needs a persistent pending count so older requests remain discoverable.',
    motion: ['Composer grows with layout spring', 'Choices reveal in sequence', 'Draft returns after send'],
    verdict: 'Best expression of the vision',
  },
  d: {
    id: 'd',
    number: '04',
    name: 'Compact run ledger',
    short: 'Ledger',
    icon: ListChecks,
    thesis: 'Treat warnings and tool runs as compact history, not oversized chat messages.',
    bestFor: 'Run failures, tool calls, retries, and auditable system events.',
    watchOut: 'Not the right surface for nuanced questions or multi-field decisions.',
    motion: ['Rows arrive once', 'Duplicate count acknowledges once', 'Details expand in place'],
    verdict: 'Best for operational events',
  },
});
