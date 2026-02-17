import React from 'react';
import { Check, Timer } from 'lucide-react';
import { Badge } from './TaaliPrimitives';

export const StatsCard = ({ icon: Icon, label, value, change }) => (
  <div
    className="border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-6 hover:shadow-lg transition-shadow cursor-pointer"
    onClick={() => {}}
  >
    <Icon size={32} className="mb-4 text-[var(--taali-text)]" />
    <div className="font-mono text-sm text-[var(--taali-muted)] mb-2">{label}</div>
    <div className="text-3xl font-bold mb-1 text-[var(--taali-text)]">{value}</div>
    <div className="font-mono text-xs text-[var(--taali-muted)]">{change}</div>
  </div>
);

export const StatusBadge = ({ status }) => {
  if (status === 'completed') {
    return (
      <Badge variant="purple" className="inline-flex gap-1">
        <Check size={12} /> Completed
      </Badge>
    );
  }

  return (
    <Badge variant="warning" className="inline-flex gap-1">
      <Timer size={12} /> In Progress
    </Badge>
  );
};
