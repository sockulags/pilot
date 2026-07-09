"use client";

import { cn } from "./cn";

export type Tone = "dim" | "accent" | "cyan" | "green" | "violet" | "amber" | "red";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  children: React.ReactNode;
  /** `label` = bare mono micro-tag (route/model); `soft` = tinted pill. @default "label" */
  variant?: "label" | "soft";
  /** @default "dim" */
  tone?: Tone;
}

/**
 * Status badge. Use `label` for the bare mono route/model micro-tags in
 * the header and `soft` for the tinted state chips (live / saved / error).
 */
export function Badge({ children, variant = "label", tone = "dim", className, ...rest }: BadgeProps) {
  return (
    <span className={cn("ds-badge", `ds-badge--${variant}`, `is-${tone}`, className)} {...rest}>
      {children}
    </span>
  );
}
