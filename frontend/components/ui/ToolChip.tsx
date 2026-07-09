"use client";

import { cn } from "./cn";

export interface ToolChipProps {
  /** Tool name, e.g. "run_command". */
  name: React.ReactNode;
  /** Truncated arg summary, e.g. "cmd=pnpm test · cwd=frontend". */
  args?: React.ReactNode;
  title?: string;
  className?: string;
}

/**
 * Tool-call chip — a mono pill carrying a tool name and a truncated arg
 * summary, shown in the toolstrip under an assistant turn.
 */
export function ToolChip({ name, args, title, className }: ToolChipProps) {
  return (
    <span className={cn("ds-toolchip", className)} title={title}>
      <span className="ds-toolchip__name">{name}</span>
      {args != null && args !== "" && <span className="ds-toolchip__args">{args}</span>}
    </span>
  );
}
