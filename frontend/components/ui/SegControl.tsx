"use client";

import { useRef } from "react";
import { cn } from "./cn";

export type SegOption = string | { value: string; label: React.ReactNode; title?: string };

export interface SegControlProps {
  options: SegOption[];
  value: string;
  onChange?: (value: string) => void;
  /** Allow segments to wrap (e.g. weekday selectors). @default false */
  wrap?: boolean;
  className?: string;
  "aria-label"?: string;
}

function norm(o: SegOption): { value: string; label: React.ReactNode; title?: string } {
  return typeof o === "string" ? { value: o, label: o } : o;
}

/**
 * Segmented control — inset single-select picker for mode switches and kind
 * pickers. The active segment lifts to --panel. Exposed as a proper ARIA
 * radiogroup: arrow keys move between segments (and select), with a roving
 * tabindex so the group is a single tab stop. For a multi-select toggle
 * group (e.g. weekdays) build a role="group" of aria-pressed buttons instead.
 */
export function SegControl({ options, value, onChange, wrap = false, className, ...aria }: SegControlProps) {
  const opts = options.map(norm);
  const btns = useRef<(HTMLButtonElement | null)[]>([]);
  const hasActive = opts.some((o) => o.value === value);
  const rovingIdx = hasActive ? opts.findIndex((o) => o.value === value) : 0;

  const selectAt = (i: number) => {
    const n = opts.length;
    if (n === 0) return;
    const idx = ((i % n) + n) % n;
    onChange?.(opts[idx].value);
    btns.current[idx]?.focus();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case "ArrowRight":
      case "ArrowDown":
        e.preventDefault();
        selectAt(rovingIdx + 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        e.preventDefault();
        selectAt(rovingIdx - 1);
        break;
      case "Home":
        e.preventDefault();
        selectAt(0);
        break;
      case "End":
        e.preventDefault();
        selectAt(opts.length - 1);
        break;
    }
  };

  return (
    <div className={cn("ds-seg", wrap && "ds-seg--wrap", className)} role="radiogroup" onKeyDown={onKeyDown} {...aria}>
      {opts.map((o, i) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              btns.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={i === rovingIdx ? 0 : -1}
            className={cn("ds-seg__opt", active && "is-active")}
            title={o.title}
            onClick={() => onChange?.(o.value)}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
