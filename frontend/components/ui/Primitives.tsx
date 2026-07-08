"use client";

import { cn } from "./cn";

/* Small presentational building blocks used across product surfaces.
   Grouped here because each is a few lines; all follow the DS guideline
   cards (mono labels, control cards, key hints, session stats). */

/** Keycap for shortcut hints, e.g. <Kbd>⌘</Kbd><Kbd>K</Kbd>. */
export function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return <kbd className={cn("ds-kbd", className)}>{children}</kbd>;
}

/** Uppercase mono section label (`SENASTE PROMPTS`, `NYTT JOBB`). */
export function SectionLabel({
  children,
  className,
  ...rest
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn("ds-seclabel", className)} {...rest}>
      {children}
    </span>
  );
}

/** Label + mono value row (drawer session stats, meta readouts). */
export function Stat({ label, value, className }: { label: React.ReactNode; value: React.ReactNode; className?: string }) {
  return (
    <div className={cn("ds-stat", className)}>
      <span className="ds-stat__label">{label}</span>
      <span className="ds-stat__value">{value}</span>
    </div>
  );
}

/** Control-card surface (rounded panel + hairline). `inset` uses the
 *  translucent panel wash used by the controls grid. */
export function Card({
  inset,
  className,
  children,
  ...rest
}: { inset?: boolean } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("ds-card", inset && "ds-card--inset", className)} {...rest}>
      {children}
    </div>
  );
}

/** Card header row (title left, optional action right). */
export function CardHead({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn("ds-card__head", className)}>{children}</div>;
}

/** Centered empty state — glyph + title + hint. */
export function EmptyState({
  glyph,
  title,
  hint,
  children,
  className,
}: {
  glyph?: React.ReactNode;
  title: React.ReactNode;
  hint?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("ds-empty", className)}>
      {glyph && <span className="ds-empty__glyph" aria-hidden="true">{glyph}</span>}
      <span className="ds-empty__title">{title}</span>
      {hint && <span className="ds-empty__hint">{hint}</span>}
      {children}
    </div>
  );
}
