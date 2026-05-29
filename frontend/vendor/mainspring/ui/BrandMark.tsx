/**
 * Brand-aware mark. Renders the active brand's logo, coloured by the themed
 * --accent so it matches the brand palette. Mainspring = concentric rings;
 * Cadence = two-stroke (resolving rhythm); Taali = geometric bars.
 *
 * Brand-agnostic: the brand is passed in (defaults to the `data-brand` set on
 * <html>), so this primitive carries no app-level config import.
 */
import type { BrandSlug } from "@mainspring/tokens";

function resolveBrand(explicit?: BrandSlug): BrandSlug {
  if (explicit) return explicit;
  const attr =
    typeof document !== "undefined"
      ? document.documentElement.getAttribute("data-brand")
      : null;
  return (attr as BrandSlug) || "mainspring";
}

export function BrandMark({ size = 32, brand }: { size?: number; brand?: BrandSlug }) {
  const key = resolveBrand(brand);
  if (key === "cadence") {
    return (
      <span style={{ width: size, height: size, borderRadius: size * 0.22, background: "var(--accent)", display: "grid", placeItems: "center", flexShrink: 0 }}>
        <svg viewBox="0 0 24 24" width={size * 0.6} height={size * 0.6} fill="none" stroke="#FFFCF6" strokeWidth={2.4} strokeLinecap="round" aria-label="Cadence">
          <line x1="9" y1="5.5" x2="9" y2="18.5" />
          <line x1="15" y1="5.5" x2="15" y2="13.5" />
        </svg>
      </span>
    );
  }
  if (key === "taali") {
    return (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="var(--accent)" strokeWidth={2.6} strokeLinecap="round" aria-label="Taali">
        <line x1="6" y1="5" x2="6" y2="19" />
        <line x1="11" y1="5" x2="11" y2="19" />
        <line x1="16" y1="5" x2="16" y2="19" />
        <line x1="3" y1="20" x2="20" y2="4" />
      </svg>
    );
  }
  // Mainspring — concentric rings + tang + red core.
  return (
    <svg viewBox="0 0 120 120" width={size} height={size} aria-label="Mainspring">
      <g fill="none" stroke="var(--accent)" strokeWidth={8} strokeLinecap="round">
        <circle cx="60" cy="60" r="44" />
        <circle cx="60" cy="60" r="29" />
        <circle cx="60" cy="60" r="14" />
        <line x1="94" y1="34" x2="108" y2="20" />
      </g>
      <circle cx="60" cy="60" r="7" fill="var(--red)" />
    </svg>
  );
}
