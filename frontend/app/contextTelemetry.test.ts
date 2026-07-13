import { describe, expect, it } from "vitest";
import {
  affectedContextCalls,
  contextMeter,
  type ContextTelemetry,
} from "./contextTelemetry";

function report(overrides: Partial<ContextTelemetry["calls"][number]> = {}): ContextTelemetry {
  const call = {
    call_index: 0,
    model: "gemma4:12b",
    context_role: "synthesis",
    declared_max: 262144,
    effective_limit: 16384,
    prompt_budget: 14336,
    estimated_prompt_tokens: 7000,
    actual_prompt_tokens: 6400,
    actual_completion_tokens: 300,
    completion_reserve: 2048,
    measurement: "exact" as const,
    categories: {
      system: 500, tools: 1000, media: 0, history: 3900, memory: 400, evidence: 600,
    },
    pressure: "normal" as const,
    compacted: false,
    overflow_retry: false,
    changes: {
      history: { summarized: 0, dropped: 0 },
      evidence: { summarized: 0, dropped: 0 },
      tools: { trimmed: 0 },
    },
    ...overrides,
  };
  return {
    version: 1,
    calls: [call],
    primary_call: 0,
    final_call: 0,
    aggregation: "per_call_not_summed",
    compacted: call.compacted,
    overflow_retried: call.overflow_retry,
  };
}

describe("contextMeter", () => {
  it("uses exact provider usage and backend effective denominator", () => {
    expect(contextMeter(report())).toMatchObject({
      state: "normal", used: 6400, denominator: 16384, percent: 39,
    });
  });
  it("falls back to the backend estimate", () => {
    expect(contextMeter(report({ actual_prompt_tokens: null, measurement: "estimated" })))
      .toMatchObject({ state: "estimated", used: 7000 });
  });
  it("surfaces backend pressure as near-limit", () => {
    expect(contextMeter(report({ pressure: "trim_tools" })).state).toBe("near_limit");
  });
  it("surfaces compaction and overflow retry", () => {
    expect(contextMeter(report({ compacted: true })).state).toBe("compacted");
    expect(contextMeter(report({ compacted: true, overflow_retry: true })).state).toBe("retried");
  });
  it("has a stable legacy state without inventing a denominator", () => {
    expect(contextMeter()).toEqual({
      state: "missing", call: null, used: 0, denominator: 0, percent: 0,
    });
  });
  it("rejects unsupported or invalid reports as legacy data", () => {
    expect(contextMeter({ ...report(), version: 2 })).toMatchObject({ state: "missing" });
    expect(contextMeter(report({ effective_limit: 0 }))).toMatchObject({ state: "missing" });
  });
  it("does not attribute an earlier call's recovery to the primary call", () => {
    const multi = report();
    const primary = { ...multi.calls[0], call_index: 1 };
    const earlier = {
      ...multi.calls[0],
      call_index: 0,
      model: "classifier-model",
      context_role: "classifier",
      compacted: true,
      overflow_retry: true,
    };
    multi.calls = [earlier, primary];
    multi.primary_call = 1;
    multi.final_call = 1;
    multi.compacted = true;
    multi.overflow_retried = true;

    expect(contextMeter(multi).state).toBe("normal");
    expect(affectedContextCalls(multi)).toEqual([earlier]);
  });
});
