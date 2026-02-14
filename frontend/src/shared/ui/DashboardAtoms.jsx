import React from 'react';
import { Check, Timer } from 'lucide-react';

export const StatsCard = ({ icon: Icon, label, value, change }) => (
  <div
    className="border-2 border-black bg-white p-6 hover:shadow-lg transition-shadow cursor-pointer"
    onClick={() => {}}
  >
    <Icon size={32} className="mb-4" />
    <div className="font-mono text-sm text-gray-600 mb-2">{label}</div>
    <div className="text-3xl font-bold mb-1">{value}</div>
    <div className="font-mono text-xs text-gray-500">{change}</div>
  </div>
);

export const StatusBadge = ({ status }) => {
  if (status === 'completed') {
    return (
      <span
        className="inline-flex items-center gap-1 px-3 py-1 text-xs font-mono font-bold border-2"
        style={{ borderColor: '#9D00FF', backgroundColor: '#f3e8ff', color: '#9D00FF' }}
      >
        <Check size={12} /> Completed
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 px-3 py-1 text-xs font-mono font-bold border-2 border-black bg-yellow-300 text-black">
      <Timer size={12} /> In Progress
    </span>
  );
};
