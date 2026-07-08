"use client";

import { cloneElement, isValidElement, useId } from "react";
import { cn } from "./cn";

export interface TooltipProps {
  label: React.ReactNode;
  /** @default "top" */
  side?: "top" | "bottom" | "left" | "right";
  children: React.ReactNode;
  className?: string;
}

/**
 * Tooltip — a hover/focus label on a dark chip. Wraps any single focusable
 * trigger; the bubble shows on hover and on keyboard focus-within (CSS-driven,
 * no JS positioning). The label is wired onto the *trigger* via
 * aria-describedby (a description, not a name — the trigger must still carry
 * its own accessible name, e.g. an aria-label on an IconButton).
 */
export function Tooltip({ label, side = "top", children, className }: TooltipProps) {
  const id = useId();

  // Attach aria-describedby to the focusable child, not the wrapper span
  // (which is not in the accessibility action path), so AT announces the
  // bubble when focus lands on the trigger. Merge with any existing value.
  const child = isValidElement<{ "aria-describedby"?: string }>(children)
    ? cloneElement(children, {
        "aria-describedby": [children.props["aria-describedby"], id].filter(Boolean).join(" "),
      })
    : children;

  return (
    <span className={cn("ds-tooltip", className)}>
      {child}
      <span role="tooltip" id={id} className={cn("ds-tooltip__bubble", `ds-tooltip__bubble--${side}`)}>
        {label}
      </span>
    </span>
  );
}
