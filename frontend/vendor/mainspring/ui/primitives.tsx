/**
 * Thin themed wrappers over the @mainspring/ui component-layer classes
 * (styles/components.css). They carry no colour of their own — the palette
 * comes from the token CSS vars, so they re-skin per brand automatically.
 */
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "ghost" | "warn" | "danger";

const BTN_CLASS: Record<ButtonVariant, string> = {
  primary: "btn-primary",
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
