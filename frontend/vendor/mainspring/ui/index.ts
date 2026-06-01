/**
 * @mainspring/ui — brand-themeable primitives + shared app infra.
 *
 * Components carry no palette of their own; they read the @mainspring/tokens
 * CSS vars, so `data-brand` on <html> re-skins them. Pair with the component
 * layer: `@import "@mainspring/ui/styles/components.css";`.
 */
export {
  Button,
  Pill,
  Card,
  Panel,
  Badge,
  Input,
  PageHero,
  KpiStrip,
  KpiTile,
} from "./primitives";
export { BrandMark } from "./BrandMark";
export { SpringMark } from "./SpringMark";
export { CommandBar } from "./CommandBar";
export type { CommandItem } from "./CommandBar";
export { ErrorBoundary } from "./ErrorBoundary";
export { ToastProvider, useToast } from "./ToastContext";
