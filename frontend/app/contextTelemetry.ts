export type ContextCategory = "system" | "tools" | "media" | "history" | "memory" | "evidence";

export type ContextCall = {
  call_index: number;
  model?: string | null;
  context_role?: string | null;
  declared_max?: number | null;
  effective_limit: number;
  prompt_budget: number;
  estimated_prompt_tokens: number;
  actual_prompt_tokens?: number | null;
  actual_completion_tokens?: number | null;
  completion_reserve: number;
  measurement: "exact" | "estimated";
  categories: Record<ContextCategory, number>;
  pressure: "normal" | "trim_tools" | "summarize_history" | "essential_only";
  compacted: boolean;
  overflow_retry: boolean;
  changes: {
    history: { summarized: number; dropped: number };
    evidence?: { summarized: number; dropped: number };
    tools: { trimmed: number };
  };
};

export type ContextTelemetry = {
  version: number;
  calls: ContextCall[];
  primary_call: number;
  final_call: number;
  aggregation: "per_call_not_summed";
  compacted: boolean;
  overflow_retried: boolean;
};

export function primaryContextCall(report?: ContextTelemetry): ContextCall | null {
  if (report?.version !== 1 || !Array.isArray(report.calls) || !report.calls.length) return null;
  return report.calls[report.primary_call] ?? report.calls.at(-1) ?? null;
}

export function affectedContextCalls(report?: ContextTelemetry): ContextCall[] {
  if (report?.version !== 1 || !Array.isArray(report.calls)) return [];
  return report.calls.filter(
    (call, index) => index !== report.primary_call && (call.compacted || call.overflow_retry),
  );
}

export function contextMeter(report?: ContextTelemetry) {
  const call = primaryContextCall(report);
  if (!call || !Number.isFinite(call.effective_limit) || call.effective_limit <= 0) {
    return { state: "missing" as const, call: null, used: 0, denominator: 0, percent: 0 };
  }
  const used = call.actual_prompt_tokens ?? call.estimated_prompt_tokens;
  const state = call.overflow_retry
    ? "retried" as const
    : call.compacted
      ? "compacted" as const
      : call.pressure !== "normal"
        ? "near_limit" as const
        : call.measurement === "estimated" ? "estimated" as const : "normal" as const;
  return {
    state,
    call,
    used,
    denominator: call.effective_limit,
    percent: Math.min(100, Math.round((used / call.effective_limit) * 100)),
  };
}
