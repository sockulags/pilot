"use client";

import { cn } from "./cn";

export type Tab = string | { value: string; label: React.ReactNode };

export interface TabsProps {
  tabs: Tab[];
  value: string;
  onChange?: (value: string) => void;
  className?: string;
  "aria-label"?: string;
}

function norm(t: Tab): { value: string; label: React.ReactNode } {
  return typeof t === "string" ? { value: t, label: t } : t;
}

/**
 * Tabs — an underline tab bar for in-content navigation; the active tab
 * carries a gradient underline. For mutually-exclusive setting toggles use
 * SegControl instead.
 */
export function Tabs({ tabs, value, onChange, className, ...aria }: TabsProps) {
  return (
    <div className={cn("ds-tabs", className)} role="tablist" {...aria}>
      {tabs.map((raw) => {
        const t = norm(raw);
        const active = t.value === value;
        return (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={active}
            className={cn("ds-tab", active && "is-active")}
            onClick={() => onChange?.(t.value)}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
