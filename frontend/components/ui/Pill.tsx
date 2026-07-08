"use client";

import { cn } from "./cn";

export type OrbTone = "grad" | "green" | "amber" | "red" | "cyan";

export interface PillProps {
  children: React.ReactNode;
  /** Orb colour. @default "grad" */
  orb?: OrbTone;
  /** Animate the orb (model thinking). @default false */
  busy?: boolean;
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  title?: string;
  className?: string;
}

/**
 * Bordered status pill with a gradient/colored orb — Pilot's brain (model)
 * indicator and connection chip. Renders as a button when interactive,
 * otherwise a plain span. `busy` makes the orb breathe.
 */
export function Pill({ children, orb = "grad", busy = false, onClick, title, className }: PillProps) {
  const orbEl = (
    <span className={cn("ds-pill__orb", orb !== "grad" && `is-${orb}`)} aria-hidden="true" />
  );
  const classes = cn("ds-pill", busy && "is-busy", className);

  if (onClick) {
    return (
      <button type="button" className={classes} onClick={onClick} title={title}>
        {orbEl}
        <span>{children}</span>
      </button>
    );
  }
  return (
    <span className={classes} title={title}>
      {orbEl}
      <span>{children}</span>
    </span>
  );
}
