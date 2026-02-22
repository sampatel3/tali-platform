import React from 'react';

export const TableRowSkeleton = ({ cols = 5 }) => (
  <tr className="animate-pulse border-b border-[var(--taali-border)]">
    {Array.from({ length: cols }).map((_, index) => (
      <td key={index} className="px-4 py-3">
        <div className="h-4 bg-[var(--taali-border)] rounded w-3/4" />
      </td>
    ))}
  </tr>
);

export const CardSkeleton = ({ lines = 3 }) => (
  <div className="animate-pulse border-2 border-[var(--taali-border)] p-6 bg-[var(--taali-surface)]">
    {Array.from({ length: lines }).map((_, index) => (
      <div
        key={index}
        className={`h-4 bg-[var(--taali-border)] rounded mb-3 ${index === 0 ? 'w-1/2' : 'w-full'}`}
      />
    ))}
  </div>
);

export const StatCardSkeleton = () => (
  <div className="animate-pulse border-2 border-[var(--taali-border)] bg-[var(--taali-surface)] p-6">
    <div className="h-8 w-8 bg-[var(--taali-border)] rounded mb-4" />
    <div className="h-3 w-24 bg-[var(--taali-border)] rounded mb-2" />
    <div className="h-8 w-16 bg-[var(--taali-border)] rounded mb-1" />
  </div>
);

