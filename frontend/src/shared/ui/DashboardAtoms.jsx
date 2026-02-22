import React from 'react';
import { AlertCircle, Check, Clock, Timer, XCircle } from 'lucide-react';
import { Badge } from './TaaliPrimitives';

export const StatsCard = ({ icon: Icon, label, value, subValue, change, onClick }) => (
  <div
    className={[
      'border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-6 hover:shadow-lg transition-shadow',
      onClick ? 'cursor-pointer' : 'cursor-default',
    ].join(' ')}
    onClick={onClick}
  >
    <Icon size={32} className="mb-4 text-[var(--taali-text)]" />
    <div className="font-mono text-sm text-[var(--taali-muted)] mb-2">{label}</div>
    <div className="text-3xl font-bold mb-1 text-[var(--taali-text)]">{value}</div>
    {subValue ? (
      <div className="font-mono text-xs text-[var(--taali-muted)] mb-1">{subValue}</div>
    ) : null}
    <div className="font-mono text-xs text-[var(--taali-muted)]">{change}</div>
  </div>
);

export const StatusBadge = ({ status }) => {
  const normalized = String(status || '').toLowerCase();

  if (normalized === 'completed') {
    return (
      <Badge variant="purple" className="inline-flex gap-1">
        <Check size={12} /> Completed
      </Badge>
    );
  }

  if (normalized === 'completed_due_to_timeout') {
    return (
      <Badge variant="warning" className="inline-flex gap-1">
        <Clock size={12} /> Timed Out
      </Badge>
    );
  }

  if (normalized === 'in_progress') {
    return (
      <Badge variant="warning" className="inline-flex gap-1">
        <Timer size={12} /> In Progress
      </Badge>
    );
  }

  if (normalized === 'expired') {
    return (
      <Badge variant="danger" className="inline-flex gap-1">
        <XCircle size={12} /> Expired
      </Badge>
    );
  }

  return (
    <Badge variant="muted" className="inline-flex gap-1">
      <AlertCircle size={12} /> Invited
    </Badge>
  );
};
