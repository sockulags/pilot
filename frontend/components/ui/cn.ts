// Tiny classname joiner for the Pilot design system. Filters out falsy
// values so components can write `cn("ds-btn", active && "is-active", className)`
// without a runtime dependency (no clsx/classnames pulled in).
export type ClassValue = string | number | false | null | undefined;

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}
