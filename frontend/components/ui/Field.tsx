"use client";

import { forwardRef } from "react";
import { cn } from "./cn";

type BaseProps = {
  fullWidth?: boolean;
  invalid?: boolean;
  className?: string;
};

export type FieldProps = BaseProps &
  (
    | ({ multiline: true; rows?: number } & React.TextareaHTMLAttributes<HTMLTextAreaElement>)
    // The input branch explicitly `never`s the textarea-only attributes so the
    // union actually discriminates at the JSX call site — `<Field rows={4} />`
    // (no `multiline`) is a type error instead of silently leaking rows onto
    // <input> (review 2026-07-05).
    | ({ multiline?: false; rows?: never; cols?: never; wrap?: never } & React.InputHTMLAttributes<HTMLInputElement>)
  );

/**
 * Text input / textarea. Inset --panel-2 fill with a hairline→accent focus
 * shift and a soft focus glow. Set `multiline` for a resizable textarea.
 */
export const Field = forwardRef<HTMLInputElement | HTMLTextAreaElement, FieldProps>(function Field(
  { fullWidth, invalid, className, ...rest },
  ref
) {
  const classes = cn("ds-field", fullWidth && "ds-field--full", className);
  const ariaInvalid = invalid ? true : undefined;

  if (rest.multiline) {
    const { multiline: _m, rows = 3, ...textareaProps } = rest as Extract<FieldProps, { multiline: true }>;
    return (
      <textarea
        ref={ref as React.Ref<HTMLTextAreaElement>}
        className={classes}
        rows={rows}
        aria-invalid={ariaInvalid}
        {...textareaProps}
      />
    );
  }
  const { multiline: _m, ...inputProps } = rest as Extract<FieldProps, { multiline?: false }>;
  return (
    <input
      ref={ref as React.Ref<HTMLInputElement>}
      className={classes}
      aria-invalid={ariaInvalid}
      {...inputProps}
    />
  );
});
