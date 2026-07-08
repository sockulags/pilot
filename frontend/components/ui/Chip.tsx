"use client";

import { cn } from "./cn";

export interface ChipProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  children: React.ReactNode;
  /** `ghost` for neutral empty-state prompts, `reply` for accent-tinted
   *  quick replies. @default "ghost" */
  variant?: "ghost" | "reply";
}

/**
 * Pill-shaped suggestion / quick-reply chip. Ghosts seed the empty state;
 * replies are the accent-tinted follow-ups under an answer.
 */
export function Chip({ children, variant = "ghost", className, ...rest }: ChipProps) {
  return (
    <button type="button" className={cn("ds-chip", `ds-chip--${variant}`, className)} {...rest}>
      {children}
    </button>
  );
}
