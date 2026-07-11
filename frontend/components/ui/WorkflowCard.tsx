"use client";

import { cn } from "./cn";

export type WorkflowTone = "accent" | "cyan" | "violet" | "green" | "amber";

export interface WorkflowCardProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type" | "title"> {
  /** Unicode glyph in the tinted tile, e.g. "▣", "⌘", "✦". */
  glyph: React.ReactNode;
  /** Tile tint. @default "accent" */
  tone?: WorkflowTone;
  title: React.ReactNode;
  /** Mono subtitle, e.g. "se & klicka". */
  subtitle?: React.ReactNode;
}

/**
 * WorkflowCard — quick-start card for the empty state. A tinted glyph tile
 * + title + mono subtitle; lifts on hover. Seeds one of Pilot's core flows
 * (dator / kod / research).
 */
export function WorkflowCard({ glyph, tone = "accent", title, subtitle, className, ...rest }: WorkflowCardProps) {
  return (
    <button type="button" className={cn("ds-workflow", className)} {...rest}>
      <span className={cn("ds-workflow__glyph", tone !== "accent" && `is-${tone}`)} aria-hidden="true">
        {glyph}
      </span>
      <span>
        <span className="ds-workflow__title">{title}</span>
        {subtitle && <span className="ds-workflow__sub">{subtitle}</span>}
      </span>
    </button>
  );
}
