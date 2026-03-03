import React from 'react';
import { BRAND } from '../../config/brand';

export const BrandGlyph = ({
  borderClass = 'border-[var(--taali-border-soft)]',
  sizeClass = 'w-10 h-10',
  markSizeClass = 'w-[1.8rem] h-[1.8rem]',
}) => (
  <div
    className={`${sizeClass} ${borderClass} flex items-center justify-center rounded-2xl border bg-[linear-gradient(145deg,var(--taali-purple),#6f53ff)] shadow-[var(--taali-shadow-soft)]`}
    aria-hidden="true"
  >
    <svg viewBox="0 0 24 24" className={markSizeClass} fill="none">
      <path
        d="M6 4.5v15M10 4.5v15M14 4.5v15M18 4.5v15M4 18.5L20 5.5"
        stroke="#FFFFFF"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  </div>
);

export const Logo = ({ onClick }) => (
  <div className="flex cursor-pointer items-center gap-3" onClick={onClick}>
    <BrandGlyph />
    <span className="taali-display text-xl font-semibold tracking-tight text-[var(--taali-text)]">{BRAND.name}</span>
  </div>
);
