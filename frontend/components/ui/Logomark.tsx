"use client";

import { cn } from "./cn";

export interface LogomarkProps {
  /** @default "md" (27px header mark). "lg" is the 56px hero badge. */
  size?: "sm" | "md" | "lg";
  /** Show the "Pilot" wordmark beside the mark. @default false */
  wordmark?: boolean;
  /** Use the conic gradient (reserved for the large hero badge). @default false */
  conic?: boolean;
  className?: string;
}

/**
 * The Pilot logomark: the ✦ glyph (U+2726) centered in a gradient square,
 * optionally with the "Pilot" wordmark. No binary asset — text + CSS
 * gradient, faithful to the DS iconography guide.
 */
export function Logomark({ size = "md", wordmark = false, conic = false, className }: LogomarkProps) {
  return (
    <span className={cn("ds-logo", className)}>
      <span
        className={cn("ds-logo__mark", `ds-logo__mark--${size}`, conic && "ds-logo__mark--conic")}
        aria-hidden="true"
      >
        ✦
      </span>
      {wordmark && <span className="ds-logo__word">Pilot</span>}
    </span>
  );
}
