"use client";

import { forwardRef } from "react";
import { cn } from "./cn";

export interface IconButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type" | "children"> {
  /** Unicode glyph, e.g. "☰", "⏰", "⟲". */
  glyph: React.ReactNode;
  /** Optional count badge (top-right). Hidden when 0/undefined. */
  badge?: number | string;
  /** @default "md" */
  size?: "sm" | "md" | "lg";
  active?: boolean;
}

/**
 * Square icon button for the top bar / toolbars. Holds one unicode glyph
 * (Pilot ships no icon font — see the DS iconography guide) plus an
 * optional count badge.
 */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { glyph, badge, size = "md", active = false, className, ...rest },
  ref
) {
  const showBadge = badge !== undefined && badge !== 0 && badge !== "0";
  return (
    <button
      ref={ref}
      type="button"
      className={cn("ds-iconbtn", `ds-iconbtn--${size}`, active && "is-active", className)}
      {...rest}
    >
      <span aria-hidden="true">{glyph}</span>
      {showBadge && <span className="ds-iconbtn__badge">{badge}</span>}
    </button>
  );
});
