import { afterEach, describe, expect, it, vi } from "vitest";
import {
  agentLabel,
  historyToTranscript,
  makeSessionId,
  modelLabel,
  preview,
  transcriptSignature,
  wsUrl,
  type ModelOption,
} from "./page";
import { t } from "./strings";

// A monotonically increasing id source, mirroring the real caller which hands
// historyToTranscript a counter so every rebuilt item gets a unique id.
function idFactory(): () => number {
  let n = 0;
  return () => (n += 1);
}

describe("wsUrl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("falls back to the local backend when there is no window (SSR)", () => {
    // The node test environment has no window, exercising the SSR branch.
    expect(wsUrl()).toBe("ws://localhost:8000/ws");
  });

  it("routes to the backend port during local dev on :3000", () => {
    vi.stubGlobal("window", {
      location: { protocol: "http:", hostname: "localhost", host: "localhost:3000", port: "3000" },
    });
    expect(wsUrl()).toBe("ws://localhost:8000/ws");
  });

  it("derives a same-host ws:// url over plain http", () => {
    vi.stubGlobal("window", {
      location: { protocol: "http:", hostname: "example.com", host: "example.com", port: "" },
    });
    expect(wsUrl()).toBe("ws://example.com/ws");
  });

  it("upgrades to wss:// when the page is served over https", () => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", hostname: "pilot.app", host: "pilot.app", port: "443" },
    });
    expect(wsUrl()).toBe("wss://pilot.app/ws");
  });
});

describe("makeSessionId", () => {
  it("returns a non-empty string id", () => {
    const id = makeSessionId();
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(0);
  });

  it("produces unique ids across calls", () => {
    const ids = new Set(Array.from({ length: 50 }, () => makeSessionId()));
    expect(ids.size).toBe(50);
  });
});

describe("preview", () => {
  it("collapses whitespace and trims short text unchanged", () => {
    expect(preview("  hello\n\tworld  ")).toBe("hello world");
  });

  it("returns text at exactly max unchanged", () => {
    const text = "x".repeat(72);
    expect(preview(text)).toBe(text);
    expect(preview(text)).toHaveLength(72);
  });

  it("truncates over-max text to max-1 chars plus an ellipsis", () => {
    const text = "y".repeat(100);
    const out = preview(text);
    expect(out).toHaveLength(72);
    expect(out).toBe(`${"y".repeat(71)}…`);
  });

  it("honors a custom max", () => {
    expect(preview("abcdef", 3)).toBe("ab…");
    expect(preview("abc", 3)).toBe("abc");
  });
});

describe("modelLabel", () => {
  const models: ModelOption[] = [
    { id: "gemma", label: "Gemma 12B", hint: "" },
    { id: "qwen", label: "Qwen 7B", hint: "" },
  ];

  it("maps the auto sentinel to its display label", () => {
    expect(modelLabel("auto", models)).toBe("Auto");
  });

  it("resolves a known model id to its configured label", () => {
    expect(modelLabel("qwen", models)).toBe("Qwen 7B");
  });

  it("falls back to the raw id when the model is unknown", () => {
    expect(modelLabel("mystery-model", models)).toBe("mystery-model");
    expect(modelLabel("gemma", [])).toBe("gemma");
  });
});

describe("agentLabel", () => {
  it("resolves a known agent to its configured label", () => {
    const claude = t.agents.find((a) => a.id === "claude");
    expect(agentLabel("claude")).toBe(claude?.label);
    expect(agentLabel("codex")).toBe(t.agents.find((a) => a.id === "codex")?.label);
  });

  it("falls back to the raw agent value when unknown", () => {
    // Cast because Agent is a closed union; the runtime fallback still applies.
    expect(agentLabel("nonexistent" as never)).toBe("nonexistent");
  });
});

describe("historyToTranscript", () => {
  it("returns an empty array for empty history", () => {
    expect(historyToTranscript([], idFactory())).toEqual([]);
  });

  it("uses an explicit numeric turn field directly", () => {
    const items = historyToTranscript(
      [{ role: "user", content: "hi", turn: 5 }],
      idFactory(),
    );
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ kind: "user", turn: 5, text: "hi" });
  });

  it("assigns unique ids from the supplied counter", () => {
    const items = historyToTranscript(
      [
        { role: "user", content: "a", turn: 1 },
        { role: "assistant", content: "b", turn: 1 },
      ],
      idFactory(),
    );
    expect(items.map((i) => i.id)).toEqual([1, 2]);
  });

  it("falls back to a positional counter that increments on user messages", () => {
    const items = historyToTranscript(
      [
        { role: "user", content: "q1" },
        { role: "assistant", content: "a1" },
        { role: "user", content: "q2" },
        { role: "assistant", content: "a2" },
      ],
      idFactory(),
    );
    // First user -> turn 1, its assistant reply shares turn 1; second exchange -> 2.
    expect(items.map((i) => i.turn)).toEqual([1, 1, 2, 2]);
  });

  it("keeps mixed turn/no-turn history non-colliding instead of collapsing onto 0", () => {
    const items = historyToTranscript(
      [
        { role: "user", content: "q1", turn: 1 },
        { role: "assistant", content: "a1", turn: 1 },
        // Legacy messages without a turn field must not reuse turn 1 or drop to 0.
        { role: "user", content: "q2" },
        { role: "assistant", content: "a2" },
      ],
      idFactory(),
    );
    const turns = items.map((i) => i.turn);
    expect(turns).toEqual([1, 1, 2, 2]);
    expect(turns).not.toContain(0);
    // Every distinct exchange keys to its own turn number.
    expect(new Set(turns).size).toBe(2);
  });

  it("maps user and assistant roles to their transcript kinds", () => {
    const items = historyToTranscript(
      [
        { role: "user", content: "ask", turn: 1 },
        { role: "assistant", content: "reply", turn: 1, cwd: "/repo" },
      ],
      idFactory(),
    );
    expect(items[0].kind).toBe("user");
    expect(items[1]).toMatchObject({ kind: "assistant", text: "reply", done: true, cwd: "/repo" });
  });
});

describe("transcriptSignature", () => {
  const base = [
    { role: "user", content: "hello" },
    { role: "assistant", content: "world" },
  ];

  it("produces equal signatures for identical role/content/telemetry", () => {
    const a = transcriptSignature(base.map((m) => ({ ...m })));
    const b = transcriptSignature(base.map((m) => ({ ...m })));
    expect(a).toBe(b);
  });

  it("changes the signature when content differs", () => {
    const a = transcriptSignature(base);
    const b = transcriptSignature([
      { role: "user", content: "hello" },
      { role: "assistant", content: "different" },
    ]);
    expect(a).not.toBe(b);
  });

  it("changes the signature when contextTelemetry differs", () => {
    const withTel = transcriptSignature([
      { role: "assistant", content: "x", contextTelemetry: { version: 1 } as never },
    ]);
    const withoutTel = transcriptSignature([{ role: "assistant", content: "x" }]);
    const otherTel = transcriptSignature([
      { role: "assistant", content: "x", contextTelemetry: { version: 2 } as never },
    ]);
    expect(withTel).not.toBe(withoutTel);
    expect(withTel).not.toBe(otherTel);
  });
});
