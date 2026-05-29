/**
 * @mainspring/tokens — typed token metadata.
 *
 * The CSS variables in tokens.css are the runtime source of truth; this
 * module exposes the brand list + accent swatches so brand configs and
 * preview/Storybook surfaces can introspect the palette without parsing CSS.
 */

export type BrandSlug = "mainspring" | "taali" | "cadence";

export const BRANDS: readonly BrandSlug[] = ["mainspring", "taali", "cadence"];

/** Whether the brand's default surface is a light or dark canvas. */
export const BRAND_SCHEME: Record<BrandSlug, "light" | "dark"> = {
  mainspring: "dark",
  taali: "light",
  cadence: "light",
};

/** Primary accent per brand, as `#RRGGBB` (mirrors tokens.css --accent). */
export const BRAND_ACCENT: Record<BrandSlug, string> = {
  mainspring: "#3ECF8E",
  taali: "#5E3AA8",
  cadence: "#E07A6E",
};

/** Apply a brand palette by setting `data-brand` on the document root. */
export function applyBrand(slug: BrandSlug, el: HTMLElement = document.documentElement): void {
  el.setAttribute("data-brand", slug);
}
