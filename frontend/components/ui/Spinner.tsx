"use client";

import { cn } from "./cn";

export interface SpinnerProps {
  /** @default "md" */
  size?: "sm" | "md" | "lg";
  /** Accessible label; omit for a purely decorative spinner. */
  label?: string;
  className?: string;
}

/**
 * Insyn / thinking spinner — a small rotating ring used while the model is
 * working. Honors prefers-reduced-motion via the global reset. (My own
 * component: the DS specs the thinking loop but ships no standalone spinner.)
 */
export function Spinner({ size = "md", label, className }: SpinnerProps) {
  return (
    <span
      className={cn("ds-spinner", `ds-spinner--${size}`, className)}
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
