"use client";

import { forwardRef } from "react";
import { cn } from "./cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "type"> {
  /** Visual weight. Gradient "primary" is reserved for the single most
   *  important action on a surface. @default "secondary" */
  variant?: ButtonVariant;
  /** @default "md" */
  size?: ButtonSize;
  fullWidth?: boolean;
  type?: "button" | "submit" | "reset";
}

/**
 * Pilot primary action button. Three weights + a danger outline. The
 * gradient variant carries the brand and should appear at most once per
 * surface (send, add project, save).
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", fullWidth = false, type = "button", className, children, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn("ds-btn", `ds-btn--${variant}`, `ds-btn--${size}`, fullWidth && "ds-btn--full", className)}
      {...rest}
    >
      {children}
    </button>
  );
});
