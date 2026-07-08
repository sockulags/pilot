"use client";

import { cn } from "./cn";

export type SelectOption = string | { value: string; label: React.ReactNode; title?: string };

export interface SelectProps {
  options: SelectOption[];
  value: string;
  onChange?: (value: string) => void;
  disabled?: boolean;
  fullWidth?: boolean;
  title?: string;
  className?: string;
  "aria-label"?: string;
}

function norm(o: SelectOption): { value: string; label: React.ReactNode; title?: string } {
  return typeof o === "string" ? { value: o, label: o } : o;
}

/**
 * Native <select> styled to match Pilot fields, with a custom ▾ chevron.
 * Native keeps the mobile picker + keyboard behaviour for free.
 */
export function Select({ options, value, onChange, disabled, fullWidth, title, className, ...aria }: SelectProps) {
  return (
    <div className={cn("ds-select", fullWidth && "ds-select--full", className)}>
      <select
        className="ds-select__el"
        value={value}
        disabled={disabled}
        title={title}
        onChange={(e) => onChange?.(e.target.value)}
        {...aria}
      >
        {options.map((raw) => {
          const o = norm(raw);
          return (
            <option key={o.value} value={o.value} title={o.title}>
              {o.label}
            </option>
          );
        })}
      </select>
      <span className="ds-select__chevron" aria-hidden="true">▾</span>
    </div>
  );
}
