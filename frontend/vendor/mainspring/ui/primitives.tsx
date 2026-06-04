/**
 * Thin themed wrappers over the @mainspring/ui component-layer classes
 * (styles/components.css). They carry no colour of their own — the palette
 * comes from the token CSS vars, so they re-skin per brand automatically.
 */
import type {
  ButtonHTMLAttributes,
  CSSProperties,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
} from "react";

type ButtonVariant = "primary" | "secondary" | "ghost" | "warn" | "danger";

const BTN_CLASS: Record<ButtonVariant, string> = {
  primary: "btn-primary",
  secondary: "btn-secondary",
  ghost: "btn-ghost",
  warn: "btn-warn",
  danger: "btn-danger",
};

export function Button({
  variant = "primary",
  className = "",
  children,
  ...rest
}: { variant?: ButtonVariant } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={`${BTN_CLASS[variant]} ${className}`.trim()} {...rest}>
      {children}
    </button>
  );
}

export function Pill({
  className = "",
  children,
  ...rest
}: { children: ReactNode } & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={`pill ${className}`.trim()} {...rest}>
      {children}
    </span>
  );
}

export function Card({
  light = false,
  className = "",
  children,
  ...rest
}: { light?: boolean; children: ReactNode } & HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`${light ? "card-light" : "card"} ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
}

export function Panel({
  className = "",
  children,
  ...rest
}: { children: ReactNode } & HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`panel ${className}`.trim()} {...rest}>
      {children}
    </div>
  );
}

type BadgeVariant =
  | "accent"
  | "muted"
  | "success"
  | "warning"
  | "danger"
  | "info";

const BADGE_CLASS: Record<BadgeVariant, string> = {
  accent: "badge-accent",
  muted: "badge-muted",
  success: "badge-success",
  warning: "badge-warning",
  danger: "badge-danger",
  info: "badge-info",
};

export function Badge({
  variant = "muted",
  className = "",
  children,
  ...rest
}: { variant?: BadgeVariant; children: ReactNode } & HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={`badge ${BADGE_CLASS[variant]} ${className}`.trim()} {...rest}>
      {children}
    </span>
  );
}

export function Input({
  className = "",
  ...rest
}: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`input ${className}`.trim()} {...rest} />;
}

/**
 * The signature dark hero slab (Taali's `.agent-header`). Token-driven, so it
 * renders deep-purple for taali and coral for cadence. Renders a kicker /
 * title / subtitle stack with an optional actions row; the trailing accent
 * "." is applied automatically when `period` is set.
 */
export function PageHero({
  kicker,
  title,
  period = false,
  subtitle,
  actions,
  className = "",
  children,
}: {
  kicker?: ReactNode;
  title: ReactNode;
  period?: boolean;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <header className={`agent-header ${className}`.trim()}>
      <div className="agent-header-inner">
        <div className="mc-page">
          {kicker && <span className="mc-kicker">{kicker}</span>}
          <h1 className="mc-h-display">
            {title}
            {period && <span className="mc-period">.</span>}
          </h1>
          {subtitle && <p className="mc-subtitle">{subtitle}</p>}
          {actions && <div className="mc-hero-actions">{actions}</div>}
          {children}
        </div>
      </div>
    </header>
  );
}

/** The shared in-app KPI grid (Taali's `.kpi-strip`). */
export function KpiStrip({
  cols,
  className = "",
  children,
  ...rest
}: { cols?: number; children: ReactNode } & HTMLAttributes<HTMLDivElement>) {
  const style = cols
    ? ({ ["--kpi-cols" as string]: String(cols) } as CSSProperties)
    : undefined;
  return (
    <div className={`kpi-strip ${className}`.trim()} style={style} {...rest}>
      {children}
    </div>
  );
}

/** A single KPI tile. `emph` glows the accent for the one action tile. */
export function KpiTile({
  label,
  value,
  detail,
  emph = false,
  className = "",
}: {
  label: ReactNode;
  value: ReactNode;
  detail?: ReactNode;
  emph?: boolean;
  className?: string;
}) {
  return (
    <div className={`kpi-tile ${emph ? "is-emph" : ""} ${className}`.trim()}>
      <div className="kpi-l">{label}</div>
      <div className="kpi-v">{value}</div>
      {detail && <div className="kpi-d">{detail}</div>}
    </div>
  );
}
