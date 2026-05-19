/**
 * Card — the atomic surface in the app. Lives at /components/ui/Card.tsx,
 * which is where future component primitives go (Button, TagPill, etc.).
 *
 * Uses semantic theme tokens from tailwind.config.ts (surface, border, ink).
 * No raw color utilities — keeps the palette swap painless.
 *
 * Variants:
 *   - default: white surface, light border
 *   - selected: primary-tinted background for active rows
 *   - subtle: muted surface for non-interactive containers (panels, sidebars)
 *
 * Padding scale: sm/md/lg
 */

import type { ReactNode } from "react";

type Variant = "default" | "selected" | "subtle";
type Pad = "none" | "sm" | "md" | "lg";

const variantClass: Record<Variant, string> = {
  default: "bg-surface border border-border hover:bg-surface-muted",
  selected: "bg-primary-muted border border-primary",
  subtle: "bg-surface-muted border border-border",
};

const padClass: Record<Pad, string> = {
  none: "",
  sm: "p-2",
  md: "p-3",
  lg: "p-4",
};

export function Card({
  children,
  variant = "default",
  pad = "md",
  className = "",
}: {
  children: ReactNode;
  variant?: Variant;
  pad?: Pad;
  className?: string;
}) {
  return (
    <div className={`rounded ${variantClass[variant]} ${padClass[pad]} ${className}`}>
      {children}
    </div>
  );
}
