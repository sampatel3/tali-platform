import React from 'react';

export const TableRowSkeleton = ({ cols = 5 }) => (
  <tr className="animate-pulse border-b border-[var(--taali-border)]">
    {Array.from({ length: cols }).map((_, index) => (
      <td key={index} className="px-3 py-2.5">
        <div className="h-4 w-3/4 rounded-[var(--taali-radius-control)] bg-[var(--taali-border)]" />
      </td>
    ))}
  </tr>
);

export const CardSkeleton = ({ lines = 3 }) => (
  <div className="animate-pulse rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4 shadow-[var(--taali-shadow-soft)]">
    {Array.from({ length: lines }).map((_, index) => (
      <div
        key={index}
        className={`mb-3 h-4 rounded-[var(--taali-radius-control)] bg-[var(--taali-border)] ${index === 0 ? 'w-1/2' : 'w-full'}`}
      />
    ))}
  </div>
);

export const StatCardSkeleton = () => (
  <div className="animate-pulse rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] p-4 shadow-[var(--taali-shadow-soft)]">
    <div className="mb-3 h-6 w-6 rounded-[var(--taali-radius-control)] bg-[var(--taali-border)]" />
    <div className="mb-2 h-3 w-24 rounded-[var(--taali-radius-control)] bg-[var(--taali-border)]" />
    <div className="mb-1 h-7 w-16 rounded-[var(--taali-radius-control)] bg-[var(--taali-border)]" />
  </div>
);
